import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AlasMirrorUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "static/js/mirror.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static/css/mirror.css").read_text(encoding="utf-8")

    @classmethod
    def function_source(cls, name):
        start = cls.script.index(f"function {name}(")
        if cls.script[max(0, start - 6) : start] == "async ":
            start -= 6
        opening_paren = cls.script.index("(", start)
        paren_depth = 0
        quote = None
        escaped = False
        closing_paren = None
        for index in range(opening_paren, len(cls.script)):
            char = cls.script[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in ("'", '"', "`"):
                quote = char
            elif char == "(":
                paren_depth += 1
            elif char == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    closing_paren = index
                    break
        if closing_paren is None:
            raise AssertionError(f"unterminated JavaScript parameters: {name}")
        brace = cls.script.index("{", closing_paren)
        depth = 0
        quote = None
        escaped = False
        for index in range(brace, len(cls.script)):
            char = cls.script[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in ("'", '"', "`"):
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return cls.script[start : index + 1]
        raise AssertionError(f"unterminated JavaScript function: {name}")

    @classmethod
    def run_node(cls, function_names, expression, prelude=""):
        definitions = "\n".join(cls.function_source(name) for name in function_names)
        program = (
            f"{prelude}\n{definitions}\n"
            f"Promise.resolve({expression})"
            ".then(result=>console.log(JSON.stringify(result)))"
            ".catch(error=>{console.error(error);process.exitCode=1;});"
        )
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_drawer_uses_an_accessible_native_config_selector(self):
        for token in (
            'id="alasConfigPicker"',
            'for="alasConfigSelect"',
            'id="alasConfigSelect"',
            'aria-describedby="alasConfigHint"',
            'id="alasConfigState"',
            'id="alasRuntimeState"',
            'role="status"',
            'id="alasOpenLink"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.template)
        self.assertNotIn('role="listbox"', self.template)
        self.assertIn(".alas-config-picker select", self.styles)
        self.assertIn("min-height: 44px", self.styles)

    def test_catalog_is_lazy_and_selection_is_scoped_to_the_username(self):
        self.assertIn("'/api/alas/configs'", self.script)
        self.assertIn("const ALAS_CONFIG_KEY_PREFIX = 'scrcpygate:alas:selected-config:'", self.script)
        self.assertIn("encodeURIComponent(username)", self.script)
        self.assertIn("const allowed=new Set(configs.map(item=>item.config_name))", self.script)
        self.assertIn("allowed.has(stored)", self.script)
        self.assertIn("allowed.has(current)", self.script)
        self.assertIn("allowed.has(serverDefault)", self.script)
        self.assertIn("if (panel && panel.classList.contains('open') && !state.alasConfigsLoaded)", self.script)

    def test_every_alas_operation_is_pinned_to_the_selected_config(self):
        self.assertIn("`/api/alas/status?config=${encodeURIComponent(configName)}`", self.script)
        self.assertIn("body:{config_name:configName}", self.script)
        self.assertIn("`/alas/embed/?config=${encodeURIComponent(binding.config_name)}`", self.script)
        self.assertIn("binding.can_run ? '可启停' : '仅查看运行状态'", self.script)
        self.assertIn("binding.can_edit ? '可编辑配置' : '不可编辑配置'", self.script)
        self.assertIn("管理员未授予此配置的启停权限", self.script)

    def test_switching_aborts_stale_status_and_old_responses_cannot_render(self):
        self.assertIn("invalidateAlasStatusRequest()", self.script)
        self.assertIn("isAlasStatusResponseCurrent(requestEpoch", self.script)
        self.assertIn("state.alasSwitching=true", self.script)
        self.assertIn("正在切换到 ${state.selectedAlasConfig}", self.script)
        self.assertIn("if (document.hidden || !state.eventConnected || state.pageLeaving) return", self.script)

    def test_loading_empty_error_and_stable_layout_states_are_present(self):
        for message in (
            "正在读取可用配置",
            "未授权任何 ALAS 配置",
            "配置列表加载失败",
            "ALAS Runtime 暂时不可达",
        ):
            with self.subTest(message=message):
                self.assertIn(message, self.template + self.script)
        self.assertIn(".alas-config-region", self.styles)
        self.assertIn("min-height: 52px", self.styles)
        self.assertIn("overflow-wrap: anywhere", self.styles)
        self.assertIn("function renderAlasConfigSummary", self.script)
        self.assertIn(".alas-config-state.is-config-summary", self.styles)
        self.assertIn("width: fit-content", self.styles)
        self.assertIn("alas-config-state__name", self.script + self.styles)

    def test_pure_catalog_selection_and_request_guards_execute(self):
        result = self.run_node(
            (
                "sameAlasCatalog",
                "selectAuthorizedAlasConfig",
                "mayStartAlasStatus",
                "isAlasStatusResponseCurrent",
                "isAlasOperationCurrent",
            ),
            """(() => {
              const configs=[
                {config_name:'Main',can_run:true,can_edit:false,is_default:true},
                {config_name:'Archive',can_run:false,can_edit:true,is_default:false}
              ];
              return {
                keepsCurrent:selectAuthorizedAlasConfig(configs,'Archive','Main','Main'),
                restoresStored:selectAuthorizedAlasConfig(configs,'Removed','Archive','Main'),
                fallsBackDefault:selectAuthorizedAlasConfig(configs,'Removed','Missing','Main'),
                empty:selectAuthorizedAlasConfig([],'Removed','Archive','Main'),
                same:sameAlasCatalog(configs, configs.map(item=>({...item}))),
                permissionChange:sameAlasCatalog(configs, [
                  {...configs[0],can_run:false}, configs[1]
                ]),
                backgroundBlocked:mayStartAlasStatus(true, false),
                explicitAllowed:mayStartAlasStatus(true, true),
                staleStatus:isAlasStatusResponseCurrent(3,4,'Main','Main'),
                wrongConfig:isAlasStatusResponseCurrent(4,4,'Main','Archive'),
                currentStatus:isAlasStatusResponseCurrent(4,4,'Main','Main'),
                staleOperation:isAlasOperationCurrent(7,8,'Main','Main'),
                currentOperation:isAlasOperationCurrent(8,8,'Main','Main')
              };
            })()""",
        )
        self.assertEqual(result["keepsCurrent"], "Archive")
        self.assertEqual(result["restoresStored"], "Archive")
        self.assertEqual(result["fallsBackDefault"], "Main")
        self.assertEqual(result["empty"], "")
        self.assertTrue(result["same"])
        self.assertFalse(result["permissionChange"])
        self.assertFalse(result["backgroundBlocked"])
        self.assertTrue(result["explicitAllowed"])
        self.assertFalse(result["staleStatus"])
        self.assertFalse(result["wrongConfig"])
        self.assertTrue(result["currentStatus"])
        self.assertFalse(result["staleOperation"])
        self.assertTrue(result["currentOperation"])

    def test_catalog_failure_executes_fail_closed_without_deleting_preference(self):
        prelude = """
          let persisted=0;
          let statusInvalidations=0;
          let operationInvalidations=0;
          let renders=0;
          const state={
            alasConfigs:[{config_name:'Main'}], alasConfigsLoaded:false,
            alasConfigsLoading:true, alasConfigsError:'', selectedAlasConfig:'Main',
            alas:null, alasStatusLoading:true, alasStatusError:'', alasSwitching:true
          };
          function replaceAlasConfigCatalog(configs){ state.alasConfigs=configs; }
          function invalidateAlasStatusRequest(){ statusInvalidations+=1; }
          function invalidateAlasOperation(){ operationInvalidations+=1; }
          function scheduleRender(){ renders+=1; }
          function persistAlasConfig(){ persisted+=1; }
        """
        result = self.run_node(
            ("handleAlasCatalogFailure",),
            """(() => {
              handleAlasCatalogFailure(new Error('offline'));
              return {persisted,statusInvalidations,operationInvalidations,renders,state};
            })()""",
            prelude,
        )
        self.assertEqual(result["persisted"], 0)
        self.assertEqual(result["statusInvalidations"], 1)
        self.assertEqual(result["operationInvalidations"], 1)
        self.assertEqual(result["state"]["selectedAlasConfig"], "")
        self.assertEqual(result["state"]["alasConfigs"], [])
        self.assertEqual(result["state"]["alasConfigsError"], "offline")
        self.assertNotIn("persistAlasConfig", self.function_source("handleAlasCatalogFailure"))

    def test_select_render_executes_without_option_mutation_for_same_catalog(self):
        prelude = """
          let appended=0;
          let removed=0;
          let created=0;
          let attributeWrites=0;
          const options=[
            {value:'Main',textContent:'Main（默认）',remove(){removed+=1;}},
            {value:'Archive',textContent:'Archive',remove(){removed+=1;}}
          ];
          const select={
            options,
            dataset:{catalogRevision:'5'},
            value:'Main',
            disabled:false,
            attributes:{'aria-busy':'false'},
            appendChild(){appended+=1;},
            getAttribute(name){return this.attributes[name] ?? null;},
            setAttribute(name,value){attributeWrites+=1;this.attributes[name]=value;}
          };
          const state={
            alasCatalogRevision:5,
            alasConfigs:[
              {config_name:'Main',is_default:true},
              {config_name:'Archive',is_default:false}
            ],
            selectedAlasConfig:'Main',
            alasStatusLoading:false
          };
          const $=()=>select;
          const actionBusy=()=>false;
          const document={createElement(){created+=1;return {};}};
        """
        result = self.run_node(
            ("syncAlasConfigSelect",),
            """(() => {
              syncAlasConfigSelect();
              const first={appended,removed,created,attributeWrites};
              state.alasStatusLoading=true;
              syncAlasConfigSelect();
              return {first,second:{appended,removed,created,attributeWrites}};
            })()""",
            prelude,
        )
        self.assertEqual(result["first"], {"appended": 0, "removed": 0, "created": 0, "attributeWrites": 0})
        self.assertEqual(result["second"]["appended"], 0)
        self.assertEqual(result["second"]["removed"], 0)
        self.assertEqual(result["second"]["created"], 0)
        self.assertEqual(result["second"]["attributeWrites"], 1)

    def test_background_refresh_only_targets_current_status(self):
        body = self.function_source("loadDynamicStatus")
        self.assertIn("loadAlasStatus(force)", body)
        self.assertNotIn("loadAlasPanel", body)
        status_body = self.function_source("loadAlasStatus")
        self.assertIn("mayStartAlasStatus(actionBusy('alas')", status_body)
        toggle_body = self.function_source("toggleAlas")
        self.assertGreaterEqual(toggle_body.count("invalidateAlasStatusRequest()"), 2)
        self.assertIn("isAlasOperationCurrent(operationSeq", toggle_body)

    def test_catalog_revocation_during_toggle_clears_busy_state_and_refreshes_new_config(self):
        prelude = """
          const actionRequests=new Map();
          const statusCalls=[];
          const persisted=[];
          let resolveToggle;
          const toggleResponse=new Promise(resolve=>{resolveToggle=resolve;});
          const state={
            alasConfigs:[{config_name:'A',can_run:true,can_edit:false,is_default:true}],
            alasConfigsLoaded:true, alasConfigsLoading:false, alasConfigsError:'',
            alasCatalogRevision:1, selectedAlasConfig:'A', alas:{config:'A',status:'running'},
            alasStatusLoading:false, alasStatusError:'', alasSwitching:false,
            alasStatusRefreshPending:false, alasStatusEpoch:0, alasOperationSeq:0,
            mirrorError:''
          };
          function setActionBusy(element,busy){element.disabled=!!busy;}
          function scheduleRender(){}
          function invalidateResource(){}
          function readStoredAlasConfig(){return 'A';}
          function persistAlasConfig(value){persisted.push(value);}
          function fetchJson(){return toggleResponse;}
          function show(){}
          function loadAlasStatus(force){
            statusCalls.push({config:state.selectedAlasConfig,force});
            state.alasStatusRefreshPending=false;
            state.alasStatusLoading=true;
            return Promise.resolve().then(()=>{
              state.alas={config:state.selectedAlasConfig,status:'stopped'};
              state.alasStatusLoading=false;
              return state.alas;
            });
          }
        """
        result = self.run_node(
            (
                "actionBusy",
                "runBusyAction",
                "currentAlasBinding",
                "sameAlasCatalog",
                "selectAuthorizedAlasConfig",
                "isAlasOperationCurrent",
                "replaceAlasConfigCatalog",
                "invalidateAlasStatusRequest",
                "invalidateAlasOperation",
                "normalizeAlasConfigBindings",
                "applyAlasConfigCatalog",
                "flushPendingAlasStatusRefresh",
                "toggleAlas",
            ),
            """(async () => {
              const button={disabled:false};
              const operation=runBusyAction('alas',button,'toggle',toggleAlas);
              await Promise.resolve();
              applyAlasConfigCatalog({
                configs:[{config_name:'B',can_run:true,can_edit:true,is_default:true}],
                default_config:'B'
              });
              const during={
                selected:state.selectedAlasConfig,
                loading:state.alasStatusLoading,
                switching:state.alasSwitching,
                pending:state.alasStatusRefreshPending,
                busy:actionBusy('alas')
              };
              resolveToggle({ok:true,alas:{config:'A',status:'stopped'}});
              await operation;
              await Promise.resolve();
              return {
                during,
                final:{
                  selected:state.selectedAlasConfig,
                  loading:state.alasStatusLoading,
                  pending:state.alasStatusRefreshPending,
                  busy:actionBusy('alas'),
                  statusConfig:state.alas && state.alas.config
                },
                statusCalls,
                persisted
              };
            })()""",
            prelude,
        )
        self.assertEqual(
            result["during"],
            {"selected": "B", "loading": False, "switching": False, "pending": True, "busy": True},
        )
        self.assertEqual(result["statusCalls"], [{"config": "B", "force": True}])
        self.assertEqual(
            result["final"],
            {"selected": "B", "loading": False, "pending": False, "busy": False, "statusConfig": "B"},
        )
        self.assertEqual(result["persisted"], ["B"])


if __name__ == "__main__":
    unittest.main()
