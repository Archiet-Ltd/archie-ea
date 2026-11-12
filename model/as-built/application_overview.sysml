package AsBuilt_application_overview {
    doc /* Generated from the code by scripts/sysml/as_built.py. Do not edit: a change here is lost on the next run. */
    private import EntelimVocabulary::*;

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
            :>> handlerLine = "107";
        }
    }
    part 'app/modules/my_applications/templates/my_applications/app_form.html' : Module {
        :>> sourcePath = "app/modules/my_applications/templates/my_applications/app_form.html";
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
        :>> sourceFile = "app/application_mgmt/forms.py:94";
        :>> origin = "app/services/application_status.py";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_2 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "tool-enum";
        :>> sourceFile = "app/modules/ai_chat/tools/registry.py:214";
        :>> origin = "app/services/application_status.py";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_3 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "raw-select";
        :>> sourceFile = "app/modules/my_applications/templates/my_applications/app_form.html:52";
        :>> origin = "app/services/application_status.py";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_4 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "raw-select";
        :>> sourceFile = "app/static/js/application_mgmt/application_import.js:505";
        :>> origin = "app/static/js/application_mgmt/application_import.js (options not resolved)";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_5 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "template-field";
        :>> sourceFile = "app/templates/applications/dashboard.html:333";
        :>> origin = "app/services/application_status.py";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_6 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "template-field";
        :>> sourceFile = "app/templates/applications/edit.html:130";
        :>> origin = "app/templates/applications/edit.html (written out in place)";
        :>> valueList = "";
    }
}
