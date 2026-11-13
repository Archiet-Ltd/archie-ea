package ApplicationOverviewCases {
    doc /* The tests that prove the Overview requirements. */
    private import EntelimVocabulary::*;
    private import ApplicationOverviewRequirements::*;

    verification def OverviewCase :> JourneyCase;

    verification caseChangesShown : OverviewCase {
        :>> testFile = "tests/smoke/test_application_overview_edit_journey.py";
        :>> testName = "test_architect_corrects_an_application_and_the_page_shows_it";
        objective { verify APP_001; }
    }
    verification caseOneHandler : OverviewCase {
        :>> testFile = "tests/test_application_overview_save.py";
        :>> testName = "test_one_handler_saves_the_overview";
        objective { verify APP_002; }
    }
    verification caseRefusedSave : OverviewCase {
        :>> testFile = "tests/smoke/test_application_overview_edit_journey.py";
        :>> testName = "test_a_refused_save_names_the_field_keeps_the_typing_and_saves_nothing";
        objective { verify APP_003; }
    }
    verification caseOtherOrg : OverviewCase {
        :>> testFile = "tests/test_application_overview_save.py";
        :>> testName = "test_second_organisation_cannot_save_by_id";
        objective { verify APP_004; }
    }
    verification caseOneStatusList : OverviewCase {
        :>> testFile = "tests/test_application_overview_save.py";
        :>> testName = "test_form_and_assistant_read_the_same_status_list";
        objective { verify APP_005; }
    }
}
