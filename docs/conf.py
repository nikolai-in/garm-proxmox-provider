"""Sphinx configuration."""

from datetime import datetime

project = "Garm Proxmox Provider"
author = "nikolai-in"
copyright = f"{datetime.now().year}, {author}"

language = "en"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinxcontrib.mermaid",
    "myst_parser",
]

html_theme = "furo"
html_static_path = ["_static"]
html_theme_options = {
    "light_logo": "logo-light.png",
    "dark_logo": "logo-dark.png",
}

autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

myst_enable_extensions = [
    "deflist",
    "colon_fence",
    "tasklist",
]
