from flask import Flask, render_template, request, redirect, url_for, Response , stream_with_context
from flask_sqlalchemy import SQLAlchemy
import subprocess
import os
from concurrent.futures import ThreadPoolExecutor



app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tests.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
executor = ThreadPoolExecutor()
# Models
class TestGroup(db.Model):
    __tablename__ = 'test_group'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    test_blocks = db.relationship('TestBlock', backref='group', lazy=True)

class TestBlock(db.Model):
    __tablename__ = 'test_block'
    id = db.Column(db.Integer, primary_key=True)
    commands = db.Column(db.Text, nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('test_group.id'), nullable=False)
    report_path = db.Column(db.String(120), nullable=True)
    result = db.Column(db.String(10), nullable=True)

with app.app_context():
    db.create_all()

# Routes
@app.route('/')
def index():
    groups = TestGroup.query.all()
    return render_template('index.html', groups=groups)

def add_tests_from_file(file, group):
    try:
        content = file.read().decode('utf-8')
        blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
        if not blocks:
            return "The file does not contain valid test blocks.", 400

        for block in blocks:
            test_block = TestBlock(commands=block, group=group)
            db.session.add(test_block)
        db.session.commit()
        return "Tests successfully added.", 200
    except UnicodeDecodeError:
        return "The file encoding is not supported. Please upload a UTF-8 encoded file.", 400
    except Exception as e:
        return str(e), 500



@app.route('/group/<int:group_id>', methods=['GET', 'POST'])
def view_group(group_id):
    group = TestGroup.query.get_or_404(group_id)

    if request.method == 'POST':
        # Check if adding test commands manually
        if 'commands' in request.form and request.form.get('commands').strip():
            commands = request.form.get('commands').strip()
            test_block = TestBlock(commands=commands, group=group)
            db.session.add(test_block)
            db.session.commit()
            return redirect(url_for('view_group', group_id=group_id))

        # Check if uploading a file
        elif 'file' in request.files:
            file = request.files.get('file')
            if file and file.filename.endswith('.txt'):
                result_message, status_code = add_tests_from_file(file, group)
                if status_code == 200:
                    return redirect(url_for('view_group', group_id=group_id))
                else:
                    return result_message, status_code

    test_blocks = TestBlock.query.filter_by(group_id=group.id).all()
    return render_template('group.html', group=group, test_blocks=test_blocks)

@app.route('/run/<int:block_id>')
def run_test(block_id):
    block = TestBlock.query.get_or_404(block_id)
    commands = block.commands + "\nexit\n"
    report_path = f"valgrind_reports/block_{block_id}.log"
    os.makedirs("valgrind_reports", exist_ok=True)
    
    # Updated path for minishell in a directory just above the current working directory
    minishell_path = os.path.join("..", "minishell")
    
    valgrind_cmd = [
        "valgrind", "--leak-check=full", "--show-leak-kinds=all", 
        "--log-file=" + report_path, minishell_path
    ]

    try:
        process = subprocess.Popen(valgrind_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate(input=commands)
        with open(report_path, "r") as report_file:
            content = report_file.read()
        block.result = "✅" if "definitely lost: 0 bytes" in content else "❌"
    except Exception:
        block.result = "❌"
        stdout, stderr = "", ""

    block.report_path = report_path
    db.session.commit()

    # Find the next test block
    test_blocks = TestBlock.query.filter_by(group_id=block.group_id).order_by(TestBlock.id).all()
    next_block = next((b for b in test_blocks if b.id > block_id), None)

    return render_template(
        'execution.html',
        commands=commands,
        stdout=stdout,
        stderr=stderr,
        result=block.result,
        report_path=report_path,
        group_id=block.group_id,
        block_id=block.id,
        next_block=next_block
    )

@app.route('/report/<int:block_id>')
def view_report(block_id):
    block = TestBlock.query.get_or_404(block_id)

    if not block.report_path:
        return "No report available for this test block.", 404

    if not os.path.exists(block.report_path):
        return f"The report file '{block.report_path}' does not exist.", 404

    # Read the report file
    try:
        with open(block.report_path, "r") as report_file:
            report_content = report_file.read()
    except Exception as e:
        return f"An error occurred while reading the report file: {str(e)}", 500

    # Extract key information from the report
    summary = {
        "definitely_lost": extract_memory_leak(report_content, "definitely lost"),
        "indirectly_lost": extract_memory_leak(report_content, "indirectly lost"),
        "possibly_lost": extract_memory_leak(report_content, "possibly lost"),
    }

    # Filter "still reachable" errors excluding libreadline
    still_reachable = extract_still_reachable_blocks(report_content)

    # Filter other errors excluding readline
    filtered_errors = extract_non_readline_errors(report_content)

    return render_template(
        'report.html',
        group_id=block.group_id,
        summary=summary,
        report_content=report_content,
        filtered_errors=filtered_errors,
        still_reachable=still_reachable,
    )


def extract_memory_leak(report_content, key):
    for line in report_content.splitlines():
        if key in line:
            parts = line.split(":")
            if len(parts) > 1:
                return parts[1].strip().split()[0]
    return "0 bytes"


def extract_still_reachable_blocks(report_content):
    """
    Extracts 'still reachable' memory blocks, excluding those from libreadline.
    """
    blocks = []
    capturing = False
    block_lines = []

    for line in report_content.splitlines():
        if "still reachable in loss record" in line:
            capturing = True
            block_lines = [line]  # Start capturing a block
        elif capturing and line.startswith("=="):
            capturing = False
            # Only keep blocks not related to libreadline
            if not any("libreadline.so" in bl for bl in block_lines):
                blocks.append("\n".join(block_lines))
        elif capturing:
            block_lines.append(line)

    # Add the last block if it was being captured
    if capturing and not any("libreadline.so" in bl for bl in block_lines):
        blocks.append("\n".join(block_lines))

    return blocks


def extract_non_readline_errors(report_content):
    """
    Extracts errors excluding those related to readline.
    """
    errors = []
    capturing = False
    error_buffer = []

    for line in report_content.splitlines():
        if "ERROR SUMMARY" in line:
            break
        if "==" in line:
            if capturing and error_buffer:
                if not any("readline" in e for e in error_buffer):
                    errors.append("\n".join(error_buffer))
                error_buffer = []
            capturing = True
        if capturing:
            error_buffer.append(line)

    if capturing and error_buffer and not any("readline" in e for e in error_buffer):
        errors.append("\n".join(error_buffer))

    return errors

def create_group_ut(group_name):
    if not group_name.strip():
        return "Erreur : le nom du groupe ne peut pas être vide."

    try:
        group = TestGroup(name=group_name.strip())
        db.session.add(group)
        db.session.commit()
        return 1
    except Exception as e:
        return 0

@app.route('/create_group', methods=['POST'])
def create_group():
    group_name = request.form.get('group_name', '').strip()
    create_group_ut(group_name)
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
