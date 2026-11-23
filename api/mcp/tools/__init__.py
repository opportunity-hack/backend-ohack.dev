"""MCP tool modules.

``register_all()`` imports every module in this package, which runs each
module's ``@register_tool`` decorators. A new tool module is picked up
with no registry edit: drop the file in and it is listed and callable.
"""
import importlib
import pkgutil

_registered = False


def register_all():
    # type: () -> None
    global _registered
    if _registered:
        return
    _registered = True
    for module_info in pkgutil.iter_modules(__path__):
        importlib.import_module(__name__ + "." + module_info.name)
