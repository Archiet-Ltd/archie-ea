package AsBuilt_application_overview {
    doc /* Generated from the code by scripts/sysml/as_built.py. Do not edit: a change here is lost on the next run. */
    private import EntelimVocabulary::*;

    part 'app/application_mgmt/detail_overview_routes.py' : Module {
        :>> sourcePath = "app/application_mgmt/detail_overview_routes.py";
        port 'application_mgmt.update_overview' : HttpRoutePort {
            :>> rule = "/dashboard/applications/<int:id>/overview-update";
            :>> methods = "POST";
            :>> endpoint = "application_mgmt.update_overview";
            :>> handlerFile = "app/application_mgmt/detail_overview_routes.py";
            :>> handlerLine = "197";
        }
    }
    part 'app/application_mgmt/forms.py' : Module {
        :>> sourcePath = "app/application_mgmt/forms.py";
    }
    part 'app/modules/ai_chat/tools/registry.py' : Module {
        :>> sourcePath = "app/modules/ai_chat/tools/registry.py";
    }
    part 'app/modules/applications/routes/update_routes.py' : Module {
        :>> sourcePath = "app/modules/applications/routes/update_routes.py";
        port 'unified_applications.update_overview' : HttpRoutePort {
            :>> rule = "/applications/<int:id>/overview-update";
            :>> methods = "POST";
            :>> endpoint = "unified_applications.update_overview";
            :>> handlerFile = "app/modules/applications/routes/update_routes.py";
            :>> handlerLine = "31";
        }
    }
    part 'app/modules/import_batch/v2/services/import_validation/validation_schemas_v2.py' : Module {
        :>> sourcePath = "app/modules/import_batch/v2/services/import_validation/validation_schemas_v2.py";
    }
    part 'app/modules/my_applications/crud_routes.py' : Module {
        :>> sourcePath = "app/modules/my_applications/crud_routes.py";
    }
    part 'app/modules/my_applications/templates/my_applications/app_form.html' : Module {
        :>> sourcePath = "app/modules/my_applications/templates/my_applications/app_form.html";
    }
    part 'app/schemas/api_schemas.py' : Module {
        :>> sourcePath = "app/schemas/api_schemas.py";
    }
    part 'app/services/import_validation/validation_schemas.py' : Module {
        :>> sourcePath = "app/services/import_validation/validation_schemas.py";
    }
    part 'app/static/js/application_mgmt/application_import.js' : Module {
        :>> sourcePath = "app/static/js/application_mgmt/application_import.js";
    }
    part 'app/templates/applications/edit.html' : Module {
        :>> sourcePath = "app/templates/applications/edit.html";
    }

    part 'app/templates/applications/dashboard.html' : Screen {
        :>> template = "app/templates/applications/dashboard.html";
    }
    part 'app/templates/application_mgmt/partials/_views_crud_tab.html' : Screen {
        :>> template = "app/templates/application_mgmt/partials/_views_crud_tab.html";
    }

    part vocabulary_deployment_status_1 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "form-select";
        :>> sourceFile = "app/application_mgmt/forms.py:92";
        :>> origin = "app/application_mgmt/forms.py (written out in place)";
        :>> valueList = "planned,development,testing,staging,production,deprecated,retired";
    }
    part vocabulary_deployment_status_2 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "tool-enum";
        :>> sourceFile = "app/modules/ai_chat/tools/registry.py:212";
        :>> origin = "app/modules/ai_chat/tools/registry.py (written out in place)";
        :>> valueList = "design,development,testing,production,retiring,decommissioned";
    }
    part vocabulary_deployment_status_3 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "allowed-list";
        :>> sourceFile = "app/modules/import_batch/v2/services/import_validation/validation_schemas_v2.py:55";
        :>> origin = "app/modules/import_batch/v2/services/import_validation/validation_schemas_v2.py";
        :>> valueList = "planned,development,testing,staging,production,deprecated,retired";
    }
    part vocabulary_deployment_status_4 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "allowed-list";
        :>> sourceFile = "app/modules/my_applications/crud_routes.py:35";
        :>> origin = "app/modules/my_applications/crud_routes.py";
        :>> valueList = "design,development,testing,production";
    }
    part vocabulary_deployment_status_5 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "raw-select";
        :>> sourceFile = "app/modules/my_applications/templates/my_applications/app_form.html:52";
        :>> origin = "app/modules/my_applications/crud_routes.py";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_6 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "allowed-list";
        :>> sourceFile = "app/schemas/api_schemas.py:43";
        :>> origin = "app/schemas/api_schemas.py";
        :>> valueList = "production,staging,development,deprecated,retired,planned,unknown";
    }
    part vocabulary_deployment_status_7 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "allowed-list";
        :>> sourceFile = "app/services/import_validation/validation_schemas.py:55";
        :>> origin = "app/services/import_validation/validation_schemas.py";
        :>> valueList = "planned,development,testing,staging,production,deprecated,retired";
    }
    part vocabulary_deployment_status_8 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "raw-select";
        :>> sourceFile = "app/static/js/application_mgmt/application_import.js:492";
        :>> origin = "app/static/js/application_mgmt/application_import.js (written out in place)";
        :>> valueList = "planned,development,testing,production";
    }
    part vocabulary_deployment_status_9 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "raw-select";
        :>> sourceFile = "app/templates/application_mgmt/partials/_views_crud_tab.html:55";
        :>> origin = "app/templates/application_mgmt/partials/_views_crud_tab.html (written out in place)";
        :>> valueList = "active,inactive,retired,planned,under_development,decommissioned";
    }
    part vocabulary_deployment_status_10 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "template-field";
        :>> sourceFile = "app/templates/applications/dashboard.html:320";
        :>> origin = "app/templates/applications/dashboard.html (free text)";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_11 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "template-field";
        :>> sourceFile = "app/templates/applications/edit.html:135";
        :>> origin = "app/templates/applications/edit.html (written out in place)";
        :>> valueList = "production,development,testing,staging,retired";
    }
}
