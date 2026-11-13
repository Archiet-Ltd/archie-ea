package NeedsAndRules {
    doc /* The top of every trace: what the company must deliver, and the twelve release-rule lines. */
    private import EntelimVocabulary::*;

    requirement NEED_LAUNCH : EntelimRequirement {
        doc /* The launch bars are the binding contract: nothing is built that serves none of the twelve release-rule lines. */
        :>> reqId = "NEED-LAUNCH";
        :>> parent = "";
        :>> ownerRole = "founder";
        :>> method = VerificationMethod::'inspection';
        :>> rationale = "Stops work that serves no customer need.";
        :>> asked = "Founder, 20 Oct 2026";
    }

    requirement NEED_JOB1 : EntelimRequirement {
        doc /* Job 1: the customer has a trustworthy model of the enterprise that every screen reads. */
        :>> reqId = "NEED-JOB1";
        :>> parent = "";
        :>> ownerRole = "founder";
        :>> method = VerificationMethod::'demonstration';
        :>> rationale = "The product is a trustworthy model that answers plain-language questions with citations.";
        :>> asked = "Founder, 20 Oct 2026";
    }

    requirement RR_1 : EntelimRequirement {
        doc /* Isolation: no organisation sees another organisation's data. */
        :>> reqId = "RR-1"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "security-lead";
        :>> method = VerificationMethod::'test'; :>> rationale = "A leak ends the business."; :>> asked = "Release quality lead";
    }
    requirement RR_2 : EntelimRequirement {
        doc /* Zero critical or high defects open. */
        :>> reqId = "RR-2"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "release-quality-lead";
        :>> method = VerificationMethod::'test'; :>> rationale = "A person must be able to finish their job."; :>> asked = "Release quality lead";
    }
    requirement RR_3 : EntelimRequirement {
        doc /* Agreeing figures: the same number on every screen that shows it. */
        :>> reqId = "RR-3"; :>> parent = "NEED-JOB1"; :>> ownerRole = "release-quality-lead";
        :>> method = VerificationMethod::'test'; :>> rationale = "Two numbers for one fact destroy trust in the model."; :>> asked = "Release quality lead";
    }
    requirement RR_4 : EntelimRequirement {
        doc /* Every promise delivered: each button, form and action does what it says. */
        :>> reqId = "RR-4"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "release-quality-lead";
        :>> method = VerificationMethod::'test'; :>> rationale = "A no-op button is a defect."; :>> asked = "Release quality lead";
    }
    requirement RR_5 : EntelimRequirement {
        doc /* Sign-up to first cited answer within fifteen minutes, unaided. */
        :>> reqId = "RR-5"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "product-lead";
        :>> method = VerificationMethod::'demonstration'; :>> rationale = "The health check has to sell."; :>> asked = "Release quality lead";
    }
    requirement RR_6 : EntelimRequirement {
        doc /* Evidence on every result: source, confidence and date. */
        :>> reqId = "RR-6"; :>> parent = "NEED-JOB1"; :>> ownerRole = "release-quality-lead";
        :>> method = VerificationMethod::'inspection'; :>> rationale = "A result with no source cannot be trusted."; :>> asked = "Release quality lead";
    }
    requirement RR_7 : EntelimRequirement {
        doc /* A customer can buy and cancel without talking to anyone. */
        :>> reqId = "RR-7"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "product-lead";
        :>> method = VerificationMethod::'demonstration'; :>> rationale = "Self-service is the sales model."; :>> asked = "Release quality lead";
    }
    requirement RR_8 : EntelimRequirement {
        doc /* A penetration test finds no high finding. */
        :>> reqId = "RR-8"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "security-lead";
        :>> method = VerificationMethod::'test'; :>> rationale = "Customers hand us their architecture."; :>> asked = "Release quality lead";
    }
    requirement RR_9 : EntelimRequirement {
        doc /* A restore drill has been run from a backup. */
        :>> reqId = "RR-9"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "operations-lead";
        :>> method = VerificationMethod::'demonstration'; :>> rationale = "A backup never restored is not a backup."; :>> asked = "Release quality lead";
    }
    requirement RR_10 : EntelimRequirement {
        doc /* The thirteen outputs the product promises exist and open. */
        :>> reqId = "RR-10"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "product-lead";
        :>> method = VerificationMethod::'test'; :>> rationale = "Outputs are what a customer pays for."; :>> asked = "Release quality lead";
    }
    requirement RR_11 : EntelimRequirement {
        doc /* Nothing is advertised as available that is absent. */
        :>> reqId = "RR-11"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "product-lead";
        :>> method = VerificationMethod::'inspection'; :>> rationale = "An unkept promise costs the sale."; :>> asked = "Founder, 21 Oct 2026";
    }
    requirement RR_12 : EntelimRequirement {
        doc /* One outside user finishes their job unaided. */
        :>> reqId = "RR-12"; :>> parent = "NEED-LAUNCH"; :>> ownerRole = "product-lead";
        :>> method = VerificationMethod::'demonstration'; :>> rationale = "Only a stranger shows what the team no longer sees."; :>> asked = "Release quality lead";
    }
}
