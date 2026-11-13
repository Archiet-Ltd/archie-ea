package ApplicationOverviewRequirements {
    doc /* What a person editing an application's Overview needs, in testable sentences. */
    private import EntelimVocabulary::*;

    requirement APP_001 : EntelimRequirement {
        doc /* The product shall show every change a person saves on an application's Overview edit form on the application page, and on the same page after a reload. */
        :>> reqId = "REQ-APP-001"; :>> parent = "RR-4"; :>> ruleLine = "4"; :>> job = "1";
        :>> ownerRole = "enterprise-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "A person cannot correct their own application today: the save answers 200 and changes nothing.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }

    requirement APP_002 : EntelimRequirement {
        doc /* The product shall save the Overview edit form through exactly one route. */
        :>> reqId = "REQ-APP-002"; :>> parent = "REQ-APP-001"; :>> ruleLine = "4"; :>> job = "1";
        :>> ownerRole = "product-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "Two save routes for one form let the form post to the one that fails, and let a fix land on the wrong one.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }

    requirement APP_003 : EntelimRequirement {
        doc /* The product shall refuse a save that holds an invalid value by naming the field in words, keeping what the person typed, and changing nothing in the stored record. */
        :>> reqId = "REQ-APP-003"; :>> parent = "REQ-APP-001"; :>> ruleLine = "4"; :>> job = "1";
        :>> ownerRole = "enterprise-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "A refusal with no reason reads as a broken button.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }

    requirement APP_004 : EntelimRequirement {
        doc /* The product shall refuse a save to an application that belongs to another organisation than the one the person is signed in to, whatever id the request carries. */
        :>> reqId = "REQ-APP-004"; :>> parent = "RR-1"; :>> ruleLine = "1"; :>> job = "1";
        :>> ownerRole = "security-lead"; :>> method = VerificationMethod::'test';
        :>> rationale = "The save route takes an id from the address bar.";
        :>> asked = "Release-rule line 1";
    }

    requirement APP_005 : EntelimRequirement {
        doc /* The product shall offer exactly one list of deployment statuses on the Overview form, on the create form and in the assistant's status tool. */
        :>> reqId = "REQ-APP-005"; :>> parent = "RR-3"; :>> ruleLine = "3"; :>> job = "1";
        :>> ownerRole = "product-architect"; :>> method = VerificationMethod::'test';
        :>> rationale = "Today the form takes any text, the create form offers seven values and the assistant accepts six, so the same field means three things.";
        :>> asked = "Found by walking entelim.org, 9 Nov 2026";
    }
}
