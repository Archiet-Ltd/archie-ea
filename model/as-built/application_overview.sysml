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
        :>> sourceKind = "template-field";
        :>> sourceFile = "app/templates/applications/dashboard.html:320";
        :>> origin = "app/templates/applications/dashboard.html (free text)";
        :>> valueList = "";
    }
    part vocabulary_deployment_status_4 : VocabularySource {
        :>> columnName = "deployment_status";
        :>> sourceKind = "template-field";
        :>> sourceFile = "app/templates/applications/edit.html:135";
        :>> origin = "app/templates/applications/edit.html (written out in place)";
        :>> valueList = "production,development,testing,staging,retired";
    }
}
