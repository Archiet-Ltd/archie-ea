package ApplicationOverviewArchitecture {
    doc /* How the Overview edit is meant to be built: the parts, the one save route, the one status list, and the cases that prove it. */
    private import EntelimVocabulary::*;
    private import ApplicationOverviewRequirements::*;

    action saveOverview : ProductFunction {
        doc /* Save the fields a person changed on an application's Overview. */
    }

    part saveRouteModule : Module {
        doc /* The one module that saves the Overview. It checks the organisation, validates each field and writes an audit entry. */
        :>> sourcePath = "app/modules/applications/routes/update_routes.py";
        port saveRoute : HttpRoutePort {
            :>> rule = "/applications/<int:id>/overview-update";
            :>> methods = "POST";
            :>> endpoint = "unified_applications.update_overview";
        }
        satisfy APP_002;
        satisfy APP_003;
        satisfy APP_004;
    }

    part overviewScreen : Screen {
        doc /* The application page in edit mode. Its Save button calls the one save route. */
        :>> template = "app/templates/applications/dashboard.html";
        port saveEvent : HttpClientPort {
            :>> target = "unified_applications.update_overview";
        }
        satisfy APP_001;
    }

    part statusAssistantTool : Module {
        doc /* The assistant's tool that sets an application's status. */
        :>> sourcePath = "app/modules/ai_chat/tools/registry.py";
        satisfy APP_005;
    }

    part createForm : Module {
        doc /* The create form for an application. Its deployment status choices come from the one list. */
        :>> sourcePath = "app/application_mgmt/forms.py";
        satisfy APP_005;
    }

    part statusList : Module {
        doc /* The one list of deployment statuses. The Overview form, the create form and the assistant read it. */
        :>> sourcePath = "app/services/application_status.py";
        satisfy APP_005;
    }

    interface overviewSaveCall : HttpCall connect client ::> overviewScreen.saveEvent to server ::> saveRouteModule.saveRoute;

    allocate saveOverview to saveRouteModule;

    item applicationRecord : StoredRecord {
        doc /* One application of one organisation. */
        :>> table = "application_components";
        attribute deploymentStatus : ScalarValues::String;
    }

    constraint deploymentStatusList : OneSource {
        doc /* One list of values for deployment status, and one place it lives. */
        :>> columnName = "deployment_status";
        :>> canonicalSource = "app/services/application_status.py";
    }
}
