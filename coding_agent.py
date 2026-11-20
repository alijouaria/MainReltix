
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI()

task = """
Build a simple task management web application.

Requirements:
- Use Python and Flask.
- Create a clean, modern web interface.
- Allow users to add tasks.
- Allow users to mark tasks as completed.
- Allow users to delete tasks.
- Store tasks in SQLite.
- Create all necessary project files.
- Install required dependencies.
- Run the application and test it.
- Fix any errors you encounter.
- Put finished project files in /workspace/outputs.
- Report which files you created and test results.
"""

with client:
    with client.beta.agents.sessions.create(
        agent={
            "model": "gpt-6-astra",
            "instructions": """
You are an autonomous senior software developer.

You can create files, edit code and execute
commands in your workspace.

Work independently through the requested task.
Test your implementation.
Fix errors before reporting completion.

Never claim a test passed unless you ran it.
""",
        },
        environment={
            "type": "openai_hosted",
            "container_size": "medium",
        },
        input=task,
        stream=True,
    ).with_result_collection() as stream:

        for event in stream:
            print(event.to_json(indent=None), flush=True)

        result = stream.get_final_result()

    print("\nFINAL RESULT:")
    print(result.output_text)

    print("\nSESSION ID:")
    print(result.session_id)
