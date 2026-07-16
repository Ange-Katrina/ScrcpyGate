import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminUiPerformanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")

    def test_initial_load_keeps_base_overview_independent_from_alas(self):
        self.assertIn("loadOverview()", self.source)
        self.assertIn("overview:['overview','overviewAlas']", self.source)
        self.assertIn("api('/api/admin/overview/alas'", self.source)
        self.assertIn("savedInitialTab", self.source)
        self.assertIn("then(()=>{ if(savedInitialTab !== 'overview') activateTab(savedInitialTab, false); })", self.source)
        self.assertIn("if(activeTab==='devices') refreshDeviceStatuses();\n  else scheduleDeviceStatusPoll();", self.source)
        self.assertNotIn("loadAll().catch", self.source)

    def test_tabs_defer_their_own_resources(self):
        for tab, resources in {
            "overview": "overview",
            "devices": "devices",
            "users": 'users","permissions',
            "video": "video",
            "alas": "alas",
            "logs": 'logs","runtimeLogs',
        }.items():
            normalized = self.source.replace("'", '"')
            self.assertIn(f'{tab}:["', normalized)
            self.assertIn(resources, normalized)
        self.assertIn("Promise.allSettled(names.map(name=>loadIfNeeded", self.source)

    def test_resources_are_single_flight_and_reject_stale_responses(self):
        for token in (
            "const resourceRequests = new Map()",
            "const resourceSequences = new Map()",
            "const loadedResources = new Set()",
            "new AbortController()",
            "previous.controller.abort()",
            "resourceSequences.get(name)!==sequence",
            "if(previous && !force) return previous.promise",
            "function markResourceStale(name)",
        ):
            self.assertIn(token, self.source)

    def test_full_refresh_is_parallel_and_mutations_are_domain_scoped(self):
        self.assertIn("loadedResources.has(name) || visible.has(name)", self.source)
        self.assertIn("Promise.allSettled(names.map(name=>RESOURCE_LOADERS[name]({force:true})))", self.source)
        self.assertIn("applyDevices(result)", self.source)
        self.assertIn("refreshDomains('permissions')", self.source)
        self.assertIn("refreshDomains('overview','overviewAlas','users','permissions','alas')", self.source)
        self.assertIn("refreshDomains('alas')", self.source)
        self.assertIn("await loadPermissions({force:true})", self.source)
        self.assertIn("markResourceStale('permissions')", self.source)
        self.assertIn("const visible=new Set(TAB_RESOURCES[activeTab] || [])", self.source)
        self.assertIn("if(name === 'overview' || wasLoaded || visible.has(name))", self.source)

    def test_visible_only_polling_cancels_the_exact_resources(self):
        for token in (
            "markResourceStale('deviceStatuses')",
            "markResourceStale('overviewDevices')",
            "markResourceStale('overviewAlas')",
            "if(!overviewPollingAllowed()) return stopOverviewPolling(true)",
            "Promise.allSettled(pending).then(() =>".replace(" ", ""),
            "pending.length ? pending : [loadOverview({force:true}),loadOverviewAlas({force:true})]",
            "function trackOverviewRequest(promise)",
            "!overviewRequestPromises().length",
        ):
            self.assertIn(token.replace(" ", ""), self.source.replace(" ", ""))

    def test_base_overview_cannot_overwrite_newer_live_domains(self):
        start = self.source.index("function applyOverview(data){")
        end = self.source.index("function applyOverviewAlas", start)
        function_source = self.source[start:end]
        program = f"""
const assert=require('assert');
const loadedResources=new Set(['overviewAlas','overviewDevices']);
const state={{overview:{{
  devices:[{{id:'fresh-device'}}], sessions:{{fresh:true}},
  users:[{{username:'old-user'}}], alas:{{config_statuses:[{{config:'Fresh'}}]}}
}}}};
function renderOverview(){{}}
{function_source}
applyOverview({{
  devices:[{{id:'stale-device'}}], sessions:{{stale:true}},
  users:[{{username:'new-user'}}], alas:{{config_statuses:[{{config:'Stale'}}]}}
}});
assert.deepStrictEqual(state.overview.devices,[{{id:'fresh-device'}}]);
assert.deepStrictEqual(state.overview.sessions,{{fresh:true}});
assert.deepStrictEqual(state.overview.alas,{{config_statuses:[{{config:'Fresh'}}]}});
assert.deepStrictEqual(state.overview.users,[{{username:'new-user'}}]);
console.log(JSON.stringify(state.overview));
"""
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["devices"][0]["id"], "fresh-device")

    def test_periodic_refresh_waits_for_foreground_request(self):
        start = self.source.index("async function refreshOverviewStatus(){")
        end = self.source.index("function syncOverviewPolling", start)
        function_source = self.source[start:end]
        program = f"""
const assert=require('assert');
let resolveForeground;
const foreground=new Promise(resolve=>{{ resolveForeground=resolve; }});
let loads=0;
let schedules=0;
let overviewRefreshGeneration=0;
function overviewPollingAllowed(){{ return true; }}
function overviewRequestPromises(){{ return [foreground]; }}
function loadOverview(){{ loads+=1; return Promise.resolve(); }}
function loadOverviewAlas(){{ loads+=1; return Promise.resolve(); }}
function stopOverviewPolling(){{}}
function scheduleOverviewRefresh(){{ schedules+=1; }}
function isAbortError(){{ return false; }}
{function_source}
(async()=>{{
  const refresh=refreshOverviewStatus();
  await Promise.resolve();
  assert.strictEqual(loads,0);
  assert.strictEqual(schedules,0);
  resolveForeground({{ok:true}});
  await refresh;
  assert.strictEqual(loads,0);
  assert.strictEqual(schedules,1);
  console.log(JSON.stringify({{loads,schedules}}));
}})().catch(error=>{{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(json.loads(completed.stdout), {"loads": 0, "schedules": 1})


if __name__ == "__main__":
    unittest.main()
