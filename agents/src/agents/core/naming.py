import re

__all__ = ("camel_case_to_kebab_case",)


def camel_case_to_kebab_case(name: str) -> str:
    # Port of upstream's camelCaseToKebabCase: MY_AGENT becomes my-agent.
    if name == name.upper() and name != name.lower():
        return name.lower().replace("_", "-")
    kebab = re.sub(r"[A-Z]", lambda match: f"-{match.group().lower()}", name)
    kebab = kebab.removeprefix("-")
    kebab = kebab.replace("_", "-")
    return kebab.removesuffix("-")
