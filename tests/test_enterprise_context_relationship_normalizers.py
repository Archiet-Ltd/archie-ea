def test_enterprise_context_assembler_normalizes_relationship_aliases():
    from app.services.enterprise_context_assembler import EnterpriseContextAssembler

    assert EnterpriseContextAssembler._normalize_rel_type_alias("uses") == "serving"
    assert EnterpriseContextAssembler._normalize_rel_type_alias("realisation") == "realization"
    assert EnterpriseContextAssembler._normalize_rel_type_alias("SpecialisationRelationship") == "specialization"
