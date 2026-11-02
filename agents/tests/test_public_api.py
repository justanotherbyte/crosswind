import inspect

import agents
import agents.mcp
from agents import (
    agent,
    chat,
    core,
    dynamic_agents,
    fibers,
    harness,
    lifecycle,
    models,
    observability,
    queue,
    schedules,
    sessions,
    state,
    streams,
    tasks,
    websockets,
)

SUBPACKAGES = (
    agent,
    chat,
    core,
    dynamic_agents,
    fibers,
    harness,
    lifecycle,
    models,
    observability,
    queue,
    schedules,
    sessions,
    state,
    streams,
    tasks,
    websockets,
)


def test_top_level_all_lists_every_subpackage_public_name() -> None:
    expected = {name for package in SUBPACKAGES for name in package.__all__}
    assert set(agents.__all__) == expected
    assert len(agents.__all__) == len(expected)  # no duplicates


def test_top_level_names_are_the_subpackage_objects() -> None:
    for package in SUBPACKAGES:
        for name in package.__all__:
            assert getattr(agents, name) is getattr(package, name)


def test_all_is_a_tuple() -> None:
    assert isinstance(agents.__all__, tuple)
    for package in SUBPACKAGES:
        assert isinstance(package.__all__, tuple)


def test_public_classes_and_functions_have_docstrings() -> None:
    for package in (agents, agents.mcp):
        for name in package.__all__:
            obj = getattr(package, name)
            if not (inspect.isclass(obj) or inspect.isfunction(obj)):
                continue
            doc = obj.__dict__.get("__doc__") if inspect.isclass(obj) else obj.__doc__
            # dataclasses fill a missing class docstring with their signature
            assert doc and not doc.startswith(f"{name}("), name
