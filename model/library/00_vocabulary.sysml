package EntelimVocabulary {
    doc /* The constructs the Entelim system model uses, and what each means here. */
    private import ScalarValues::*;

    /* How a requirement is proven, as NASA names the four methods. */
    enum def VerificationMethod {
        enum 'test';
        enum 'demonstration';
        enum 'inspection';
        enum 'analysis';
    }

    /* A need stated as one testable sentence, with where it came from and who owns it. */
    requirement def EntelimRequirement {
        attribute reqId : String;
        attribute parent : String;
        attribute ruleLine : String;
        attribute job : String;
        attribute ownerRole : String;
        attribute method : VerificationMethod;
        attribute rationale : String;
        attribute asked : String;
    }

    /* A source file or package that holds code. The as-built generator names each by its path. */
    part def Module {
        attribute sourcePath : String;
    }

    /* A screen: one page a person sees, with the events it offers. */
    part def Screen {
        attribute template : String;
    }

    /* An HTTP route a server offers. */
    port def HttpRoutePort {
        attribute rule : String;
        attribute methods : String;
        attribute endpoint : String;
        attribute handlerFile : String;
        attribute handlerLine : String;
    }

    /* The side of a call that asks a route to act: a form, a link or a client. */
    port def HttpClientPort {
        attribute target : String;
    }

    interface def HttpCall {
        end client : HttpClientPort;
        end server : HttpRoutePort;
    }

    /* A table a person's data is stored in, and the columns the model cares about. */
    item def StoredRecord {
        attribute table : String;
    }

    /* A function the product performs. It is allocated to exactly one part. */
    action def ProductFunction;

    /* One formula for one figure, or one list of values for one field. */
    constraint def OneSource {
        attribute columnName : String;
        attribute canonicalSource : String;
    }

    /* Where a form, a tool or a page gets the allowed values of one stored field. Generated from the code. */
    part def VocabularySource {
        attribute columnName : String;
        attribute sourceKind : String;
        attribute sourceFile : String;
        attribute origin : String;
        attribute valueList : String;
    }

    /* A test that proves a requirement. */
    verification def JourneyCase {
        attribute testFile : String;
        attribute testName : String;
    }
}
