package ApplicationOverviewRequirements {
    doc /* What a person editing an application's Overview needs, in testable sentences. */
    private import EntelimVocabulary::*;

    requirement APP_001 : EntelimRequirement {
        doc /* When a person changes fields on an application's Overview edit form and presses Save, the application page shows every change, and a reload still shows them. */
        :>> reqId = "REQ-APP-001"; :>> parent = "RR-4"; :>> ruleLine = "4"; :>> job = "1";
        :>> ownerRole = "enterprise-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "A person cannot correct their own application today: the save answers 200 and changes nothing.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }

    requirement APP_002 : EntelimRequirement {
        doc /* The Overview edit form saves through one route, and no second route saves the same form. */
        :>> reqId = "REQ-APP-002"; :>> parent = "REQ-APP-001"; :>> ruleLine = "4"; :>> job = "1";
        :>> ownerRole = "product-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "Two save routes for one form let the form post to the one that fails, and let a fix land on the wrong one.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }

    requirement APP_003 : EntelimRequirement {
        doc /* A save with an invalid value names the field in words, keeps what the person typed, and saves nothing. */
        :>> reqId = "REQ-APP-003"; :>> parent = "REQ-APP-001"; :>> ruleLine = "4"; :>> job = "1";
        :>> ownerRole = "enterprise-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "A refusal with no reason reads as a broken button.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }

    requirement APP_004 : EntelimRequirement {
        doc /* A person signed in to one organisation cannot save to another organisation's application by its id. */
        :>> reqId = "REQ-APP-004"; :>> parent = "RR-1"; :>> ruleLine = "1"; :>> job = "1";
        :>> ownerRole = "security-lead"; :>> method = VerificationMethod::'test';
        :>> rationale = "The save route takes an id from the address bar.";
        :>> asked = "Release-rule line 1";
    }

    requirement APP_005 : EntelimRequirement {
        doc /* An application's deployment status has one list of values, and the Overview form, the create form and the assistant's status tool all read that one list. */
        :>> reqId = "REQ-APP-005"; :>> parent = "RR-3"; :>> ruleLine = "3"; :>> job = "1";
        :>> ownerRole = "product-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "Today the form takes any text, the create form offers seven values and the assistant accepts six, so the same field means three things.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }
}
