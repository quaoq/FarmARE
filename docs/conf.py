# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.


# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
# import os
# import sys
# sys.path.insert(0, os.path.abspath('.'))


# import here to avoid circular dependencies
import are.simulation.apps.agent_user_interface  # noqa: F401

# -- Project information -----------------------------------------------------

project = "Meta Agents Research Environments"
copyright = "2025, Meta AI"
author = "Meta AI"


# -- General configuration ---------------------------------------------------


# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.autosummary",
    "sphinx.ext.coverage",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
    "sphinx.ext.viewcode",
    "sphinx.ext.githubpages",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinx_autodoc_typehints",
    "sphinx_favicon",
    "sphinxcontrib.googleanalytics",
    "sphinx_click",  # For CLI documentation
    "sphinxcontrib.images",  # For images
]


myst_enable_extensions = ["colon_fence"]

primary_domain = "py"

highlight_language = "python3"

autoclass_content = "both"
autodoc_class_signature = "mixed"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
}
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented_params"
autodoc_typehints_format = "short"

autosectionlabel_prefix_document = True

todo_include_todos = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "torch": ("https://pytorch.org/docs/stable/", None),
    "fsspec": ("https://filesystem-spec.readthedocs.io/en/latest/", None),
    "pydantic": ("https://docs.pydantic.dev/latest", None),
    "PIL": ("https://pillow.readthedocs.io/en/stable/", None),
}

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", ".venv"]

# The master toctree document.
master_doc = "index"

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
#
# source_suffix = ['.rst', '.md']
source_suffix = [".rst"]


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "furo"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#008080",
        "color-brand-content": "#008080",
    },
    "navigation_with_keys": True,
    "top_of_page_button": "edit",
    "sidebar_hide_name": False,
}

html_context = {
    "github_user": "facebookresearch",
    "github_repo": "meta-agents-research-environments",
}
html_show_copyright = False
html_static_path = ["_static"]
html_title = "Meta Agents Research Environments"

# Configure sidebar to show global TOC on all pages
html_sidebars = {
    "**": [
        "sidebar/scroll-start.html",
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ]
}


html_theme_options = {
    "source_repository": (
        "https://github.com/facebookresearch/meta-agents-research-environments/"
    ),
    "source_branch": "main",
    "source_directory": "docs/",
}


favicons = [
    {"href": "favicon.svg"},  # => use `_static/favicon.svg`
]

# Configuration for sphinxcontrib-images
images_config = {
    "override_image_directive": True,
    "cache": True,
    "default_show_title": True,
    "default_image_width": "100%",
    "default_image_height": "auto",
    "download": False,
    "requests_kwargs": {},
    "default_group": None,
    "show_caption": True,
}

# borrowed from https://github.com/astropy/astropy/
# many exceptions for nitpick to ignore due to python lookup issues
nitpick_ignore = []

for line in open("nitpick-exceptions"):
    if line.strip() == "" or line.startswith("#"):
        continue
    dtype, target = line.split(None, 1)
    target = target.strip()
    nitpick_ignore.append((dtype, target))

# Regex patterns for default-value strings that autodoc tries to resolve as
# class targets. These appear in upstream agent prompt templates; the values
# are literal strings, not class references, but Sphinx's typehint renderer
# emits ref-not-found warnings under -W. Match them with patterns to avoid
# listing every long prompt string explicitly.
nitpick_ignore_regex = [
    # Enum members from agent message construction
    (r"py:class", r"MessageRole\..*"),
    # Prompt-template literal strings (start with quote / bracket / escaped newline)
    (r"py:class", r"['\"\\[].{20,}"),
    (r"py:class", r"\\\\n.{20,}"),
    (r"py:class", r"one representing actions.*"),
]

# Pydantic + sphinx-autodoc-typehints emit non-actionable warnings for
# pydantic's guarded type imports and forward references that aren't
# importable in the docs build environment. Suppress them so -W doesn't fail.
suppress_warnings = [
    "sphinx_autodoc_typehints.guarded_import",
    "sphinx_autodoc_typehints.forward_reference",
]

# Google Analytics.
googleanalytics_enabled = True
googleanalytics_id = "G-1D4CB2K78X"
