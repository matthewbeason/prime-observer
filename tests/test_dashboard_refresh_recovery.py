import shutil
import subprocess
import unittest
from pathlib import Path

from test_dashboard_bucket_selection_alignment import extract_function


@unittest.skipUnless(shutil.which('node'), 'Node is required for async refresh tests')
class DashboardRefreshRecoveryTests(unittest.TestCase):
    def test_request_limit_and_status_recovery(self):
        html = (Path(__file__).resolve().parents[1] / 'viz/index.html').read_text()
        functions = '\n'.join(extract_function(html, name) for name in (
            'async function fetchLocalResource', 'function showTelemetryLoadFailure',
            'function updateHeader', 'function computeTelemetry'))
        script = '''
const assert = require('node:assert/strict');
let activeArtifactRequests=0; const artifactRequestWaiters=[];
let inFlight=0, maximum=0;
async function fetch(url) {
  inFlight++; maximum=Math.max(maximum,inFlight);
  if(url==='fail'){inFlight--;throw Error('induced failure');}
  return {clone(){return {async arrayBuffer(){
    await new Promise(resolve=>setTimeout(resolve,5));inFlight--;return new ArrayBuffer(0);
  }}}};
}
const ids=['statusState','statusLastSample','statusDot','statusRefresh','statusWindow'];
const nodes=Object.fromEntries(ids.map(id=>[id,{textContent:'',className:'',classList:{add(){}}}]));
nodes.status={firstElementChild:{}};
Object.defineProperty(nodes.status,'textContent',{set(){throw Error('status subtree destroyed');}});
const document={getElementById(id){return nodes[id]||null;}};
const REFRESH_MS=5000,WINDOW_HOURS=24,GATEWAY_HOST='gateway';
const toCompactRelativeAge=()=> 'just now';
'''+functions+'''
(async()=>{
 const outcomes=await Promise.allSettled(Array.from({length:20},(_,i)=>fetchLocalResource(i===3?'fail':'ok')));
 assert.equal(outcomes.filter(x=>x.status==='rejected').length,1);
 assert.equal(maximum,4);assert.equal(inFlight,0);assert.equal(activeArtifactRequests,0);
 showTelemetryLoadFailure();assert.equal(nodes.statusState.textContent,'Telemetry unavailable');
 const data=[{t:new Date()}];updateHeader(data);computeTelemetry(data);
 assert.equal(nodes.statusState.textContent,'Live');assert.equal(nodes.statusRefresh.textContent,'every 5s');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node'], input=script, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
