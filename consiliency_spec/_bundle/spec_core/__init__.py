"""spec_core: the spec library modules, as one package.

Modules inside this package import one another RELATIVELY only (``from ..canon import canon``),
never as ``spec_core``. The same bytes then resolve in-tree (``spec_core.idmodel.idmodel``, with
``src/`` as the one import root) and inside a vendored copy (``<host>._bundle.spec_core...``),
where an absolute ``spec_core`` import would escape the copy.
"""
