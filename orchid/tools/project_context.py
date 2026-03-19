"""Project context tool — detects language, module system, framework, and test framework."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProjectContext:
    """Holds detected project context information."""

    def __init__(
        self,
        language: str = "unknown",
        module_system: str = "unknown",
        framework: str | None = None,
        test_framework: str | None = None,
        package_manager: str | None = None,
        source: str = "unknown",
        raw: dict | None = None,
    ):
        self.language = language
        self.module_system = module_system
        self.framework = framework
        self.test_framework = test_framework
        self.package_manager = package_manager
        self.source = source
        self.raw = raw or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "module_system": self.module_system,
            "framework": self.framework,
            "test_framework": self.test_framework,
            "package_manager": self.package_manager,
            "source": self.source,
        }

    def import_syntax_hint(self) -> str:
        """Return a concise import syntax hint for agents."""
        if self.language == "javascript" or self.language == "typescript":
            if self.module_system == "esm":
                return 'Use ES module syntax: `import { x } from "./module.js"` (include .js extension)'
            elif self.module_system == "commonjs":
                return 'Use CommonJS syntax: `const x = require("./module")`'
            else:
                return "Module system unknown — prefer ES module syntax with .js extensions"
        elif self.language == "python":
            return "Use Python imports: `from module import x` or `import module`"
        return "Language unknown — use appropriate import syntax for the project"

    def to_context_block(self) -> str:
        """Return a formatted context block for injection into agent prompts."""
        lines = ["## Project Context (auto-detected)"]
        lines.append(f"- **Language**: {self.language}")
        lines.append(f"- **Module system**: {self.module_system}")
        if self.framework:
            lines.append(f"- **Framework**: {self.framework}")
        if self.test_framework:
            lines.append(f"- **Test framework**: {self.test_framework}")
        if self.package_manager:
            lines.append(f"- **Package manager**: {self.package_manager}")
        lines.append(f"- **Detected from**: {self.source}")
        lines.append("")
        lines.append(f"**Import syntax**: {self.import_syntax_hint()}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"ProjectContext(language={self.language!r}, module_system={self.module_system!r}, "
            f"framework={self.framework!r}, test_framework={self.test_framework!r})"
        )


class ProjectContextTool:
    """Tool that reads project metadata files and extracts context."""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self._cached: ProjectContext | None = None

    def get_context(self) -> ProjectContext:
        """Return cached or freshly detected project context."""
        if self._cached is None:
            self._cached = self._detect()
        return self._cached

    def project_context(self, path: str = ".") -> str:
        """
        Read project metadata and return a formatted context block.
        Detects: language, module system (esm/commonjs), framework, test framework.
        """
        ctx = self.get_context()
        return ctx.to_context_block()

    def _detect(self) -> ProjectContext:
        """Detect project context from metadata files."""
        # Try JS/TS first (package.json)
        pkg_json = self.project_path / "package.json"
        if pkg_json.exists():
            return self._from_package_json(pkg_json)

        # Try Python (pyproject.toml)
        pyproject = self.project_path / "pyproject.toml"
        if pyproject.exists():
            return self._from_pyproject_toml(pyproject)

        # Try Python (setup.py)
        setup_py = self.project_path / "setup.py"
        if setup_py.exists():
            return self._from_setup_py(setup_py)

        # Try Python (setup.cfg)
        setup_cfg = self.project_path / "setup.cfg"
        if setup_cfg.exists():
            return self._from_setup_cfg(setup_cfg)

        # Fallback: heuristic file scan
        return self._from_heuristics()

    # ------------------------------------------------------------------ #
    # JavaScript / TypeScript                                              #
    # ------------------------------------------------------------------ #

    def _from_package_json(self, pkg_json: Path) -> ProjectContext:
        """Extract context from package.json."""
        try:
            data = json.loads(pkg_json.read_text())
        except Exception as e:
            logger.warning(f"Could not parse package.json: {e}")
            return ProjectContext(language="javascript", source="package.json (parse error)")

        # Module system
        pkg_type = data.get("type", "")
        if pkg_type == "module":
            module_system = "esm"
        elif pkg_type == "commonjs":
            module_system = "commonjs"
        else:
            # Heuristic: check for .mjs files or tsconfig
            module_system = self._guess_js_module_system(data)

        # Language: TypeScript if devDependencies includes typescript
        all_deps = {
            **data.get("dependencies", {}),
            **data.get("devDependencies", {}),
            **data.get("peerDependencies", {}),
        }
        language = "typescript" if "typescript" in all_deps else "javascript"

        # Framework detection
        framework = self._detect_js_framework(all_deps)

        # Test framework detection
        test_framework = self._detect_js_test_framework(all_deps, data)

        # Package manager
        package_manager = self._detect_js_package_manager()

        return ProjectContext(
            language=language,
            module_system=module_system,
            framework=framework,
            test_framework=test_framework,
            package_manager=package_manager,
            source="package.json",
            raw=data,
        )

    def _guess_js_module_system(self, pkg_data: dict) -> str:
        """Heuristic: guess module system when 'type' field is absent."""
        # Check for .mjs files
        mjs_files = list(self.project_path.rglob("*.mjs"))
        if mjs_files:
            return "esm"

        # Check tsconfig for module setting
        tsconfig = self.project_path / "tsconfig.json"
        if tsconfig.exists():
            try:
                ts_data = json.loads(tsconfig.read_text())
                compiler = ts_data.get("compilerOptions", {})
                ts_module = compiler.get("module", "").lower()
                if ts_module in ("esnext", "es2020", "es2022", "es2015", "es6", "node16", "nodenext"):
                    return "esm"
                elif ts_module in ("commonjs",):
                    return "commonjs"
            except Exception:
                pass

        # Check scripts for "type": "module" indicators
        scripts = pkg_data.get("scripts", {})
        scripts_str = json.dumps(scripts)
        if "--experimental-vm-modules" in scripts_str or "esm" in scripts_str.lower():
            return "esm"

        # Default for Node.js without explicit type is commonjs
        return "commonjs"

    def _detect_js_framework(self, all_deps: dict) -> str | None:
        """Detect the main JS framework from dependencies."""
        framework_map = [
            # React ecosystem
            ("next", "Next.js"),
            ("react", "React"),
            ("gatsby", "Gatsby"),
            ("remix", "Remix"),
            # Vue ecosystem
            ("nuxt", "Nuxt"),
            ("vue", "Vue"),
            # Angular
            ("@angular/core", "Angular"),
            # Svelte
            ("@sveltejs/kit", "SvelteKit"),
            ("svelte", "Svelte"),
            # Backend
            ("fastify", "Fastify"),
            ("express", "Express"),
            ("koa", "Koa"),
            ("hapi", "@hapi/hapi"),
            ("nestjs", "NestJS"),
            ("@nestjs/core", "NestJS"),
            # Electron
            ("electron", "Electron"),
        ]
        for dep_key, name in framework_map:
            if dep_key in all_deps:
                return name
        return None

    def _detect_js_test_framework(self, all_deps: dict, pkg_data: dict) -> str | None:
        """Detect the JS test framework."""
        test_map = [
            ("vitest", "Vitest"),
            ("jest", "Jest"),
            ("@jest/core", "Jest"),
            ("mocha", "Mocha"),
            ("jasmine", "Jasmine"),
            ("ava", "AVA"),
            ("tap", "TAP"),
            ("playwright", "Playwright"),
            ("@playwright/test", "Playwright"),
            ("cypress", "Cypress"),
            ("puppeteer", "Puppeteer"),
        ]
        for dep_key, name in test_map:
            if dep_key in all_deps:
                return name

        # Check scripts for test runner hints
        scripts = pkg_data.get("scripts", {})
        test_script = scripts.get("test", "")
        for dep_key, name in test_map:
            if dep_key in test_script:
                return name

        return None

    def _detect_js_package_manager(self) -> str | None:
        """Detect package manager from lockfiles."""
        if (self.project_path / "pnpm-lock.yaml").exists():
            return "pnpm"
        if (self.project_path / "yarn.lock").exists():
            return "yarn"
        if (self.project_path / "bun.lockb").exists():
            return "bun"
        if (self.project_path / "package-lock.json").exists():
            return "npm"
        return None

    # ------------------------------------------------------------------ #
    # Python                                                               #
    # ------------------------------------------------------------------ #

    def _from_pyproject_toml(self, pyproject: Path) -> ProjectContext:
        """Extract context from pyproject.toml."""
        try:
            # Use tomllib (Python 3.11+) or tomli fallback
            try:
                import tomllib
                data = tomllib.loads(pyproject.read_text())
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore
                    data = tomllib.loads(pyproject.read_text())
                except ImportError:
                    # Manual minimal TOML parsing for simple cases
                    data = self._parse_toml_minimal(pyproject.read_text())
        except Exception as e:
            logger.warning(f"Could not parse pyproject.toml: {e}")
            return ProjectContext(language="python", source="pyproject.toml (parse error)")

        # Gather all dependencies
        all_deps: list[str] = []
        project_section = data.get("project", {})
        all_deps.extend(project_section.get("dependencies", []))

        tool_poetry = data.get("tool", {}).get("poetry", {})
        if tool_poetry:
            all_deps.extend(tool_poetry.get("dependencies", {}).keys())
            all_deps.extend(tool_poetry.get("dev-dependencies", {}).keys())
            for group in tool_poetry.get("group", {}).values():
                all_deps.extend(group.get("dependencies", {}).keys())

        # Optional deps
        for extras in project_section.get("optional-dependencies", {}).values():
            all_deps.extend(extras)

        deps_lower = [d.lower().split("[")[0].split(">=")[0].split("==")[0].strip() for d in all_deps]

        framework = self._detect_py_framework(deps_lower)
        test_framework = self._detect_py_test_framework(deps_lower, data)
        package_manager = self._detect_py_package_manager()

        return ProjectContext(
            language="python",
            module_system="python",
            framework=framework,
            test_framework=test_framework,
            package_manager=package_manager,
            source="pyproject.toml",
            raw=data,
        )

    def _from_setup_py(self, setup_py: Path) -> ProjectContext:
        """Extract context from setup.py (best-effort text parsing)."""
        try:
            content = setup_py.read_text()
        except Exception:
            return ProjectContext(language="python", source="setup.py (read error)")

        # Extract install_requires via regex
        import re
        deps: list[str] = []
        m = re.search(r"install_requires\s*=\s*\[([^\]]+)\]", content, re.DOTALL)
        if m:
            raw_deps = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
            deps = [d.lower().split("[")[0].split(">=")[0].split("==")[0].strip() for d in raw_deps]

        framework = self._detect_py_framework(deps)
        test_framework = self._detect_py_test_framework(deps, {})
        package_manager = self._detect_py_package_manager()

        return ProjectContext(
            language="python",
            module_system="python",
            framework=framework,
            test_framework=test_framework,
            package_manager=package_manager,
            source="setup.py",
        )

    def _from_setup_cfg(self, setup_cfg: Path) -> ProjectContext:
        """Extract context from setup.cfg."""
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(setup_cfg)
            raw_deps = cfg.get("options", "install_requires", fallback="")
            deps = [
                d.lower().split("[")[0].split(">=")[0].split("==")[0].strip()
                for d in raw_deps.splitlines()
                if d.strip()
            ]
        except Exception:
            deps = []

        framework = self._detect_py_framework(deps)
        test_framework = self._detect_py_test_framework(deps, {})
        package_manager = self._detect_py_package_manager()

        return ProjectContext(
            language="python",
            module_system="python",
            framework=framework,
            test_framework=test_framework,
            package_manager=package_manager,
            source="setup.cfg",
        )

    def _detect_py_framework(self, deps: list[str]) -> str | None:
        """Detect the main Python framework from dependency names."""
        framework_map = [
            ("fastapi", "FastAPI"),
            ("django", "Django"),
            ("flask", "Flask"),
            ("starlette", "Starlette"),
            ("tornado", "Tornado"),
            ("aiohttp", "aiohttp"),
            ("sanic", "Sanic"),
            ("falcon", "Falcon"),
            ("bottle", "Bottle"),
            ("pyramid", "Pyramid"),
            ("litestar", "Litestar"),
            ("grpcio", "gRPC"),
            ("celery", "Celery"),
            ("pydantic", "Pydantic"),
            ("sqlalchemy", "SQLAlchemy"),
            ("typer", "Typer"),
            ("click", "Click"),
            ("langchain", "LangChain"),
            ("anthropic", "Anthropic"),
            ("openai", "OpenAI"),
        ]
        for dep_key, name in framework_map:
            if dep_key in deps:
                return name
        return None

    def _detect_py_test_framework(self, deps: list[str], data: dict) -> str | None:
        """Detect the Python test framework."""
        test_map = [
            ("pytest", "pytest"),
            ("unittest2", "unittest"),
            ("nose2", "nose2"),
            ("nose", "nose"),
            ("hypothesis", "Hypothesis"),
            ("behave", "Behave"),
            ("robot", "Robot Framework"),
        ]
        for dep_key, name in test_map:
            if dep_key in deps:
                return name

        # Check pyproject.toml [tool.pytest.*] section
        if data.get("tool", {}).get("pytest"):
            return "pytest"

        # Check if pytest is in dev deps via uv/poetry
        tool = data.get("tool", {})
        for section in ("uv", "hatch", "flit"):
            dev = tool.get(section, {}).get("dev-dependencies", [])
            if any("pytest" in str(d).lower() for d in dev):
                return "pytest"

        return None

    def _detect_py_package_manager(self) -> str | None:
        """Detect Python package manager from lockfiles/config."""
        if (self.project_path / "uv.lock").exists():
            return "uv"
        if (self.project_path / "poetry.lock").exists():
            return "poetry"
        if (self.project_path / "Pipfile.lock").exists():
            return "pipenv"
        if (self.project_path / "requirements.txt").exists():
            return "pip"
        return None

    # ------------------------------------------------------------------ #
    # Heuristics fallback                                                  #
    # ------------------------------------------------------------------ #

    def _from_heuristics(self) -> ProjectContext:
        """Guess project context from file extensions and structure."""
        py_files = list(self.project_path.rglob("*.py"))
        js_files = list(self.project_path.rglob("*.js"))
        ts_files = list(self.project_path.rglob("*.ts"))

        # Exclude common noise dirs
        def _exclude(files: list[Path]) -> list[Path]:
            skip = {"node_modules", ".git", ".venv", "__pycache__", "dist", "build"}
            return [f for f in files if not any(p in skip for p in f.parts)]

        py_files = _exclude(py_files)
        js_files = _exclude(js_files)
        ts_files = _exclude(ts_files)

        if len(py_files) > len(js_files) + len(ts_files):
            return ProjectContext(
                language="python",
                module_system="python",
                source="heuristic (file scan)",
            )
        elif ts_files:
            return ProjectContext(
                language="typescript",
                module_system="esm",
                source="heuristic (file scan)",
            )
        elif js_files:
            return ProjectContext(
                language="javascript",
                module_system="commonjs",
                source="heuristic (file scan)",
            )

        return ProjectContext(source="heuristic (no files found)")

    # ------------------------------------------------------------------ #
    # Minimal TOML parser (fallback when tomllib/tomli not available)      #
    # ------------------------------------------------------------------ #

    def _parse_toml_minimal(self, content: str) -> dict:
        """Very minimal TOML parser — handles simple key=value and [sections]."""
        import re
        result: dict = {}
        current_section: list[str] = []

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Section header
            m = re.match(r"^\[([^\]]+)\]$", line)
            if m:
                current_section = [p.strip().strip('"') for p in m.group(1).split(".")]
                # Ensure nested dicts exist
                d = result
                for key in current_section:
                    d = d.setdefault(key, {})
                continue

            # Key = value
            m = re.match(r'^(\w[\w-]*)\s*=\s*(.+)$', line)
            if m:
                key, val_str = m.group(1), m.group(2).strip()
                # Parse value
                if val_str.startswith('"') or val_str.startswith("'"):
                    val: Any = val_str.strip('"\'')
                elif val_str.startswith("["):
                    # Simple list of strings
                    items = re.findall(r'["\']([^"\']+)["\']', val_str)
                    val = items
                elif val_str.lower() == "true":
                    val = True
                elif val_str.lower() == "false":
                    val = False
                else:
                    try:
                        val = int(val_str)
                    except ValueError:
                        val = val_str

                # Set in nested dict
                d = result
                for section_key in current_section:
                    d = d.setdefault(section_key, {})
                d[key] = val

        return result