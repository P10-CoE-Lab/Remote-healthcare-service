from __future__ import annotations

import jinja2
from pydantic import BaseModel


class RenderedContent(BaseModel):
    subject: str
    body: str


class TemplateRegistry:
    def __init__(self, template_dir: str) -> None:
        # Two environments: HTML auto-escape on, plain-text/JSON auto-escape off.
        # Prevents Jinja2 from mangling JSON webhook templates or SMS text.
        self._html_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_dir),
            autoescape=True,
        )
        self._plain_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_dir),
            autoescape=False,
        )

    def render(
        self,
        profile: str,
        channel: str,
        ext: str,
        context: dict,
    ) -> str:
        """
        Resolution order:
          1. templates/{profile}/{channel}.{ext}
          2. templates/{profile}/default.txt
          3. context["body"] raw string
        """
        env = self._html_env if ext == "html" else self._plain_env
        candidates = [
            f"{profile}/{channel}.{ext}",
            f"{profile}/default.txt",
        ]
        for path in candidates:
            try:
                return env.get_template(path).render(**context)
            except jinja2.TemplateNotFound:
                continue
        return context.get("body", "")
