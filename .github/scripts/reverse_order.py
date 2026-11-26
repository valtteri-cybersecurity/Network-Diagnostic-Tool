"""pytest plugin that runs the collected tests in reverse order.

Used by CI to detect tests that depend on the execution order:
PYTHONPATH=.github/scripts python -m pytest -p reverse_order
"""


def pytest_collection_modifyitems(items):
    items.reverse()
