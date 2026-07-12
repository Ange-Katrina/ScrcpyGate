import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminUiPerformanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")

    def test_initial_load_only_requests_overview(self):
        self.assertIn("loadOverview()", self.source)
        self.assertIn("savedInitialTab", self.source)
        self.assertIn("then(()=>{ if(savedInitialTab !== 'overview') activateTab(savedInitialTab, false); })", self.source)
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
        self.assertIn("refreshDomains('overview','devices','permissions')", self.source)
        self.assertIn("refreshDomains('overview','users','permissions','alas')", self.source)
        self.assertIn("refreshDomains('alas')", self.source)
        self.assertIn("refreshDomains('permissions')", self.source)
        self.assertIn("const visible=new Set(TAB_RESOURCES[activeTab] || [])", self.source)
        self.assertIn("if(name === 'overview' || wasLoaded || visible.has(name))", self.source)


if __name__ == "__main__":
    unittest.main()
