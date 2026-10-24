"""Adversarial rendering tests for trusted Enterprise Genome HTML fragments."""

from bs4 import BeautifulSoup

from app.modules.codegen.services.genome_data_ropa_emitter import render_ropa_table
from app.modules.enterprise_genome.emit.ai_systems_register import (
    emit_ai_systems_register,
)
from app.modules.enterprise_genome.services.security_matrix_emitter import (
    render_control_matrix,
)
from app.modules.genome.emit.coverage_matrix import emit_coverage_matrix_html
from app.modules.genome.emit.drift_report import emit_drift_report_html


ATTACK = '<script>alert(1)</script><img src=x onerror="alert(2)">'


def _assert_inert_fragment(fragment):
    soup = BeautifulSoup(str(fragment), "html.parser")
    assert soup.find("script") is None
    assert soup.find("img") is None
    for tag in soup.find_all(True):
        assert not any(name.lower().startswith("on") for name in tag.attrs)
    assert ATTACK in soup.get_text()


def test_ropa_fragment_escapes_modelled_text():
    fragment = render_ropa_table(
        {
            "spec_hash": ATTACK,
            "processing_activities": [
                {
                    "name": ATTACK,
                    "data_categories": [ATTACK],
                    "systems": [],
                    "lawful_basis": ATTACK,
                    "retention": ATTACK,
                    "provenance": {
                        "archimate_element_id": 1,
                        "archimate_type": ATTACK,
                    },
                }
            ],
        }
    )
    _assert_inert_fragment(fragment)


def test_ai_systems_fragment_escapes_modelled_text():
    fragment = emit_ai_systems_register(
        {
            "spec_hash": ATTACK,
            "counts": {"total": 1},
            "systems": [
                {
                    "archimate_element_id": 1,
                    "name": ATTACK,
                    "provider": ATTACK,
                    "model_id": ATTACK,
                    "model_currency": "unknown",
                    "autonomy_level": ATTACK,
                    "data_sensitivity": ATTACK,
                    "risk_flags": [ATTACK],
                    "governance": {},
                }
            ],
        }
    )
    _assert_inert_fragment(fragment)


def test_security_matrix_fragment_escapes_modelled_text():
    fragment = render_control_matrix(
        {
            "spec_hash": ATTACK,
            "store": ATTACK,
            "controls": [
                {
                    "control": {"code": ATTACK, "title": ATTACK, "category": ATTACK},
                    "framework": {"code": ATTACK, "name": ATTACK},
                    "requirement": {
                        "title": ATTACK,
                        "implementation_status": ATTACK,
                        "status": ATTACK,
                    },
                    "provenance": {
                        "archimate_element_id": ATTACK,
                        "element_name": ATTACK,
                        "archimate_type": ATTACK,
                    },
                }
            ],
        }
    )
    _assert_inert_fragment(fragment)


def test_coverage_fragment_escapes_modelled_text():
    fragment = emit_coverage_matrix_html(
        {
            "spec_hash": ATTACK,
            "capability_source": ATTACK,
            "capabilities": [{"id": 1, "name": ATTACK, "archimate_element_id": 11}],
            "applications": [{"id": 2, "name": ATTACK, "archimate_element_id": 12}],
            "cells": [
                {
                    "capability_id": 1,
                    "application_id": 2,
                    "mapping": {"support_level": ATTACK, "coverage_percentage": 50},
                    "provenance": {
                        "capability_archimate_element_id": 11,
                        "application_archimate_element_id": 12,
                    },
                }
            ],
        }
    )
    _assert_inert_fragment(fragment)


def test_drift_fragment_escapes_modelled_text():
    fragment = emit_drift_report_html(
        {
            "spec_hash": ATTACK,
            "summary": {"total": 1, "by_severity": {"high": 1}},
            "signals_scanned": ["hostile"],
            "findings": [
                {
                    "type": "hostile",
                    "severity": "high",
                    "why": ATTACK,
                    "elements": [
                        {
                            "archimate_element_id": 1,
                            "archimate_type": ATTACK,
                            "name": ATTACK,
                            "role": ATTACK,
                        }
                    ],
                    "remediation": {"available": False, "hint": ATTACK},
                }
            ],
            "uncomputable_signals": {},
        }
    )
    _assert_inert_fragment(fragment)
