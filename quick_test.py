import os
import subprocess
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

# Define the database structure
Base = declarative_base()

class TestGroup(Base):
    __tablename__ = 'test_group'
    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False, unique=True)
    test_blocks = relationship('TestBlock', backref='group', lazy=True)

class TestBlock(Base):
    __tablename__ = 'test_block'
    id = Column(Integer, primary_key=True)
    commands = Column(Text, nullable=False)
    group_id = Column(Integer, ForeignKey('test_group.id'), nullable=False)
    report_path = Column(String(120), nullable=True)
    result = Column(String(10), nullable=True)

# Set up the database connection
DATABASE_URI = 'sqlite:///instance/tests.db'
engine = create_engine(DATABASE_URI)
Session = sessionmaker(bind=engine)
session = Session()

# Function to execute tests with Valgrind
def execute_tests(group_id=None, restart_all=False):
    # Run `make re` before starting tests
    # print("Running `make re` to rebuild the project...")
    # try:
    #     subprocess.run(["make", "re"], check=True, text=True)
    #     print("`make re` completed successfully.")
    # except subprocess.CalledProcessError as e:
    #     print(f"`make re` failed with error: {e}")
    #     return
    # Fetch all tests for the group or pending tests for the group
    if group_id is None:
        print("No group selected. Exiting.")
        return

    if restart_all:
        blocks = session.query(TestBlock).filter(TestBlock.group_id == group_id).all()
        # Reset the results for all tests in the group
        for block in blocks:
            block.result = None
            block.report_path = None
        session.commit()
    else:
        blocks = session.query(TestBlock).filter(TestBlock.group_id == group_id, TestBlock.result == None).all()

    if not blocks:
        print("No pending tests found for the selected group.")
        return

    os.makedirs("valgrind_reports", exist_ok=True)

    # Updated path for minishell in a directory just above the current working directory
    minishell_path = os.path.join("..", "minishell")

    for block in blocks:
        print(f"Running TestBlock ID: {block.id}")
        commands = block.commands + "\nexit\n"
        report_path = f"valgrind_reports/block_{block.id}.log"

        valgrind_cmd = [
            "valgrind", "--leak-check=full", "--show-leak-kinds=all",
            "--log-file=" + report_path, minishell_path
        ]

        try:
            # Run Valgrind with the commands
            process = subprocess.Popen(
                valgrind_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            stdout, stderr = process.communicate(input=commands)

            # Read the Valgrind report
            with open(report_path, "r") as report_file:
                report_content = report_file.read()

            # Update the result based on Valgrind output
            block.result = "✅" if "definitely lost: 0 bytes" in report_content else "❌"
            block.report_path = report_path

            print(f"TestBlock ID {block.id} result: {block.result}")

        except Exception as e:
            block.result = "❌"
            print(f"Error running TestBlock ID {block.id}: {e}")

        # Commit the results to the database
        session.commit()

    print("All tests completed.")

if __name__ == "__main__":
    print("Available Test Groups:")
    groups = session.query(TestGroup).all()
    if not groups:
        print("No test groups found. Exiting.")
        exit()

    for group in groups:
        print(f"ID: {group.id}, Name: {group.name}")

    try:
        group_id = int(input("Enter the ID of the test group you want to run: ").strip())
        selected_group = session.query(TestGroup).get(group_id)
        if not selected_group:
            print(f"Group ID {group_id} not found. Exiting.")
            exit()
    except ValueError:
        print("Invalid input. Please enter a valid group ID. Exiting.")
        exit()

    restart = input("Do you want to restart all tests in this group (y/n)? ").strip().lower()
    restart_all = restart == 'y'

    print(f"Starting tests for group '{selected_group.name}'...")
    execute_tests(group_id=group_id, restart_all=restart_all)
    print("Test execution finished.")
