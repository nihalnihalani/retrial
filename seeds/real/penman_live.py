"""Layout test for a graph-serialization library, extracted at penman 1.2.1.

Installs its own dependency on first run so it can execute on a bare image; a
setup failure exits 3 (not 1) so it is never mistaken for a test result.

Maintainers: see seeds/real/penman_live.README.md before editing this file.
"""
import importlib
import site
import subprocess
import sys

_PIN = "penman==1.2.1"

try:
    from penman.codec import PENMANCodec
    from penman.layout import rearrange
    from penman.model import Model
except ImportError:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", _PIN],
            check=True, capture_output=True, timeout=180,
        )
        # pip installs into the user site dir, which typically does not exist when
        # this interpreter starts — so its path finders are stale and a plain
        # re-import raises ModuleNotFoundError even though the install succeeded.
        # Re-register the user site and drop the cached finders before retrying.
        for p in site.getusersitepackages() if isinstance(
                site.getusersitepackages(), list) else [site.getusersitepackages()]:
            if p not in sys.path:
                sys.path.insert(0, p)
        importlib.invalidate_caches()
        from penman.codec import PENMANCodec
        from penman.layout import rearrange
        from penman.model import Model
    except Exception as e:  # bootstrap failed -> infra error, never a test verdict
        print(f"BOOTSTRAP-FAILED {type(e).__name__}: {str(e)[:200]}")
        sys.exit(3)

codec = PENMANCodec()
model = Model()

t = codec.parse('''
    (a / alpha
       :ARG0 (b / beta
                :ARG0 (g / gamma)
                :ARG1 (d / delta))
       :ARG0-of d
       :ARG1 (e / epsilon))''')

rearrange(t, model.random_order)
expected = (
    '(a / alpha\n'
    '   :ARG0-of d\n'
    '   :ARG1 (e / epsilon)\n'
    '   :ARG0 (b / beta\n'
    '            :ARG0 (g / gamma)\n'
    '            :ARG1 (d / delta)))')
sys.exit(0 if codec.format(t) == expected else 1)
