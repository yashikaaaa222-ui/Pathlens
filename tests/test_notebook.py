"""notebooks/data_exploration.ipynb is a saved, executed document. These checks keep it honest and fresh."""
import os
import re

import nbformat
import pytest

from ai.classify_report import ReportClassifier

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "notebooks", "data_exploration.ipynb")


@pytest.fixture(scope="module")
def nb():
    return nbformat.read(PATH, as_version=4)


def code_cells(nb):
    return [c for c in nb.cells if c.cell_type == "code"]


def test_the_notebook_is_valid(nb):
    nbformat.validate(nb)


def test_every_code_cell_was_run_and_none_failed(nb):
    for cell in code_cells(nb):
        assert cell.execution_count is not None, f"not executed: {cell.source[:60]!r}"
        assert not [o for o in cell.outputs if o.output_type == "error"], f"error in: {cell.source[:60]!r}"


def test_the_charts_are_saved_in_the_file(nb):
    images = [o for c in code_cells(nb) for o in c.outputs if "image/png" in getattr(o, "data", {})]
    assert len(images) >= 8  # a reader on GitHub sees the charts without running anything


def test_it_says_up_front_that_everything_is_synthetic(nb):
    first = nb.cells[0].source
    assert "Everything here is synthetic" in first
    assert "Nothing in this notebook measures how accurate PathLens is" in first


def test_it_carries_no_machine_specific_paths(nb):
    text = open(PATH, encoding="utf-8").read()
    assert not re.search(r"/(home|tmp|root|Users)/[A-Za-z0-9_.-]+/", text)


def test_the_classifier_score_in_the_saved_output_is_still_the_real_one(nb):
    # If the seed set or the model changes, the notebook must be run again; this fails until it is.
    saved = "\n".join(o.get("text", "") for c in code_cells(nb) for o in c.outputs if o.output_type == "stream")
    current = ReportClassifier().info["cv_macro_f1"]
    assert f"The API reports the same number: {current}" in saved, "re-run the notebook: the classifier score has changed"


def test_the_open_design_question_is_written_down(nb):
    text = "\n".join(c.source for c in nb.cells if c.cell_type == "markdown")
    assert "An open design question" in text and "coverage" in text
