"""Project creation helper — directory, git init, orchid init, lifecycle state."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class ProjectCreator:
    """Creates new Orchid-managed projects from scratch."""

    def __init__(self, machine_profile=None) -> None:
        # Lazy import to avoid circular at module level
        if machine_profile is None:
            from orchid.machine_profile import MachineProfile
            machine_profile = MachineProfile.load()
        self.profile = machine_profile

    def confirm_path(self, name: str, project_type: str | None = None) -> Path:
        """Return the suggested filesystem path for a new project."""
        return self.profile.get_project_root(project_type) / name

    def create(
        self,
        name: str,
        description: str = "",
        project_type: str | None = None,
        base_dir: Path | None = None,
        git_init: bool | None = None,
    ) -> Path:
        """Create a project directory and initialise it for Orchid.

        Steps:
          1. Resolve directory from machine-profile or explicit base_dir
          2. Create directory
          3. git init (if configured)
          4. Scaffold orchid templates
          5. Initialise ProjectLifecycle at phase=NEW
          6. Return project path
        """
        if base_dir is not None:
            project_dir = Path(base_dir).expanduser().resolve() / name
        else:
            project_dir = self.confirm_path(name, project_type).resolve()

        project_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created directory: %s", project_dir)

        # ── git init ──────────────────────────────────────────────────────────
        use_git = git_init if git_init is not None else self.profile.defaults.get("git_init", True)
        if use_git and not (project_dir / ".git").exists():
            result = subprocess.run(
                ["git", "init"],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info("Git initialized in %s", project_dir)
            else:
                logger.warning("Git init failed: %s", result.stderr.strip())

        # ── orchid init ───────────────────────────────────────────────────────
        self._apply_templates(project_dir, name=name, description=description)

        # ── lifecycle state ───────────────────────────────────────────────────
        from orchid.lifecycle import ProjectLifecycle

        lifecycle = ProjectLifecycle.load(project_dir)
        lifecycle.state.project_name = name
        lifecycle.save()  # writes project.state.json at phase=NEW

        # ── annotate project_type in .orchid.yaml ─────────────────────────────
        if project_type:
            orchid_yaml = project_dir / ".orchid.yaml"
            if orchid_yaml.exists():
                content = orchid_yaml.read_text(encoding="utf-8")
                if "lifecycle:" not in content:
                    content += f"\nlifecycle:\n  project_type: {project_type}\n"
                    orchid_yaml.write_text(content, encoding="utf-8")

        return project_dir

    # ── Internal ──────────────────────────────────────────────────────────────

    def _apply_templates(self, project_dir: Path, name: str, description: str) -> None:
        """Write orchid template files; skip files that already exist."""
        templates_dir = Path(__file__).parent / "templates"
        subs = {"project_name": name, "description": description}

        for tmpl in templates_dir.iterdir():
            dest = project_dir / tmpl.name
            if dest.exists():
                continue
            content = tmpl.read_text(encoding="utf-8")
            for key, val in subs.items():
                content = content.replace("{" + key + "}", val)
            dest.write_text(content, encoding="utf-8")

        # Ensure .orchid/ is in .gitignore
        gitignore = project_dir / ".gitignore"
        entry = ".orchid/"
        if gitignore.exists():
            existing = gitignore.read_text(encoding="utf-8")
            if entry not in existing:
                gitignore.write_text(existing.rstrip() + f"\n{entry}\n", encoding="utf-8")
        else:
            gitignore.write_text(f"{entry}\n", encoding="utf-8")
