import os
import subprocess
import tempfile


def collect_requirements(test_description: str | None = None) -> dict:
    if test_description:
        return {"description": test_description}
    print("\nTestZeus Guided Builder\n")
    return {"description": input("Describe your test parameters: ")}


async def generate_gherkin(requirements, llm):
    prompt = f"""
    Convert the following into a clean Gherkin feature file.
    Rules:
    - 1 Feature only
    - 1 Scenario only
    - Use Given / When / Then properly
    - Do NOT hallucinate data or steps
    - Keep it minimal and deterministic
    INPUT:
    {requirements.get("description", requirements)}
    OUTPUT:
    ONLY a valid .feature file
    """
    result = await llm.ainvoke(prompt)
    return result.content if hasattr(result, "content") else str(result)


def save_temp_feature(feature: str):
    f = tempfile.NamedTemporaryFile(suffix=".feature", delete=False, mode="w")
    f.write(feature)
    f.close()
    return f.name


async def run_guided_mode(llm, test_description: str | None = None):
    req = collect_requirements(test_description)
    print("\nGenerating feature...\n")
    feature = await generate_gherkin(req, llm)
    feature_path = save_temp_feature(feature)
    print("\n" + "=" * 50)
    print(feature)
    print("=" * 50)
    if test_description:
        print("\nRunning generated test...\n")
        return feature_path
    while True:
        choice = input("\n[r]un  [e]dit  [g]enerate again  [q]uit: ").lower()
        if choice == "r":
            return feature_path
        elif choice == "e":
            editor = os.getenv("EDITOR", "nano")
            subprocess.run([editor, feature_path])
            with open(feature_path) as f:
                feature = f.read()
            print("\n" + "=" * 50)
            print(feature)
            print("=" * 50)
        elif choice == "g":
            feature = await generate_gherkin(req, llm)
            print("\n" + "=" * 50)
            print(feature)
            print("=" * 50)
        elif choice == "q":
            exit()
        else:
            print("Invalid choice")
