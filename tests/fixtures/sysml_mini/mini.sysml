package Mini {
    doc /* A small model: one need, one rule line, three requirements, two parts, one allocation and two cases. */
    private import EntelimVocabulary::*;

    requirement NEED_M : EntelimRequirement {
        doc /* The shop must keep its books. */
        :>> reqId = "NEED-M";
        :>> parent = "";
        :>> ownerRole = "founder";
        :>> method = VerificationMethod::'inspection';
        :>> rationale = "Without books there is no shop.";
        :>> asked = "Owner";
    }

    requirement RR_M : EntelimRequirement {
        doc /* The books are right. */
        :>> reqId = "RR-M"; :>> parent = "NEED-M"; :>> ownerRole = "owner";
        :>> method = VerificationMethod::'inspection';
        :>> rationale = "A wrong book costs money.";
        :>> asked = "Owner";
    }

    requirement REQ_M1 : EntelimRequirement {
        doc /* The shop shall total every sale on the day it is made. */
        :>> reqId = "REQ-M-1"; :>> parent = "RR-M"; :>> ruleLine = "1"; :>> job = "1";
        :>> ownerRole = "owner"; :>> method = VerificationMethod::'test';
        :>> rationale = "A late total hides a loss.";
        :>> asked = "Owner";
    }

    requirement REQ_M2 : EntelimRequirement {
        doc /* The shop shall refuse a sale with no price. */
        :>> reqId = "REQ-M-2"; :>> parent = "RR-M"; :>> ruleLine = "1"; :>> job = "1";
        :>> ownerRole = "owner"; :>> method = VerificationMethod::'test';
        :>> rationale = "A sale with no price is a gift.";
        :>> asked = "Owner";
    }

    action totalSales : ProductFunction {
        doc /* Total the day's sales. */
    }

    part tillModule : Module {
        doc /* The till. */
        :>> sourcePath = "till.py";
        port sellRoute : HttpRoutePort {
            :>> rule = "/till/sell";
            :>> methods = "POST";
            :>> endpoint = "till.sell";
        }
        satisfy REQ_M1;
        satisfy REQ_M2;
    }

    allocate totalSales to tillModule;

    verification def MiniCase :> JourneyCase;

    verification caseTotals : MiniCase {
        :>> testFile = "tests/test_till.py";
        :>> testName = "test_totals_every_sale";
        objective { verify REQ_M1; }
    }
    verification caseRefuses : MiniCase {
        :>> testFile = "tests/test_till.py";
        :>> testName = "test_refuses_a_sale_with_no_price";
        objective { verify REQ_M2; }
    }
}

package AsBuilt_mini {
    doc /* What a generator would write for the small model: the handler it found and a reader of a field's list. */
    private import EntelimVocabulary::*;

    part 'till.py' : Module {
        :>> sourcePath = "till.py";
        port 'till.sell' : HttpRoutePort {
            :>> rule = "/shop/till/sell";
            :>> methods = "POST";
            :>> endpoint = "till.sell";
            :>> handlerFile = "till.py";
            :>> handlerLine = "3";
        }
    }

    part vocabulary_status_1 : VocabularySource {
        :>> columnName = "status";
        :>> sourceKind = "form-select";
        :>> sourceFile = "forms.py:9";
        :>> origin = "forms.py (written out in place)";
        :>> valueList = "open,closed";
    }
}
