"""Read-only local RDMA readiness evidence; exit 2 means unavailable, never passed."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from prepare_data import check_outputs

BINARIES=['lab_client','lab_dynamic_client','lab_epoch_client','upstream/storage_server']
FLAGS=['CMakeFiles/'+name+'.dir/flags.make' for name in
       ['lab_client','lab_dynamic_client','lab_epoch_client','lab_strategies']]
FLAGS.append('upstream/CMakeFiles/rdma_hnsw_core.dir/flags.make')


def probe(command):
    try:
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10)
        return dict(command=list(map(str,command)),returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
    except (OSError,subprocess.TimeoutExpired) as error:
        return dict(command=list(map(str,command)),returncode=None,stdout='',stderr=str(error))


def assess(evidence):
    blockers=[]
    if evidence['platform']!='Linux':blockers.append('Linux deployment required')
    if not evidence['devices']:blockers.append('no RDMA device')
    if not evidence['cm_device']:blockers.append('/dev/infiniband/rdma_cm unavailable')
    options=evidence['backend_options']
    if any(options.get(key)!='ON' for key in ['ENABLE_RDMA','ENABLE_RDMA_CM']):
        blockers.append('RDMA and CM build options must both be ON')
    if options.get('CMAKE_BUILD_TYPE')!='Release':blockers.append('Release build required')
    flags=evidence['compiler_flags']
    if not flags or any('-O3' not in value.split() or '-DNDEBUG' not in value.split() for value in flags):
        blockers.append('inconsistent or missing Release compiler flags')
    if not evidence['binaries_present']:blockers.append('missing or non-executable client/server program')
    linkage=evidence['linkage'];text=linkage['stdout']
    if linkage['returncode']!=0 or 'not found' in text or any(lib not in text for lib in ['libibverbs.so','librdmacm.so']):
        blockers.append('actual ibverbs/librdmacm shared libraries not linked or available')
    checks=evidence.get('binary_checks',{})
    for name in BINARIES:
        check=checks.get(name,{})
        linkage=check.get('linkage',{});text=linkage.get('stdout','')
        if not check.get('executable') or linkage.get('returncode')!=0 or 'not found' in text or any(
                lib not in text for lib in ['libibverbs.so','librdmacm.so']):
            blockers.append('program is not executable with available RDMA libraries: '+name)
    return dict(local_ready=not blockers,blockers=blockers,remote_connection_verified=False)


def collect(build_dir):
    cache=build_dir/'CMakeCache.txt';options={}
    if cache.is_file():
        for line in cache.read_text(encoding='utf-8').splitlines():
            if ':' in line and '=' in line and not line.startswith('//'):
                key,value=line.split('=',1);options[key.split(':',1)[0]]=value
    devices=Path('/sys/class/infiniband')
    binary_checks={name:dict(executable=(build_dir/name).is_file() and os.access(build_dir/name,os.X_OK),
                            linkage=probe(['ldd',str(build_dir/name)])) for name in BINARIES}
    evidence=dict(platform=platform.system(),devices=sorted(p.name for p in devices.iterdir()) if devices.is_dir() else [],
                  cm_device=Path('/dev/infiniband/rdma_cm').exists(),backend_options=options,
                  compiler_flags=[(build_dir/name).read_text(encoding='utf-8') if (build_dir/name).is_file() else '' for name in FLAGS],
                  binaries_present=all(check['executable'] for check in binary_checks.values()),
                  binary_checks=binary_checks,linkage=binary_checks['lab_epoch_client']['linkage'],
                  environment={name:probe(command) for name,command in
                    [('kernel',['uname','-a']),('devices',['ibv_devices']),('provider',['ibv_devinfo']),('addresses',['ip','-brief','address'])]})
    return dict(schema='rdma_preflight_v1',checked_utc=datetime.now(timezone.utc).isoformat(),
                measurement='local_environment_check',build_dir=str(build_dir),evidence=evidence,**assess(evidence))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build=args.build_dir.resolve()
    inputs=[build/'CMakeCache.txt']+[build/path for path in BINARIES+FLAGS]
    # A report must not replace any build dependency (including hardlinks).
    inputs += [path for path in build.rglob('*') if path.is_file()]
    check_outputs(inputs,[args.output])
    if args.output.exists():raise ValueError('use a new preflight output path')
    report=collect(build);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('local_ready='+str(report['local_ready']).lower()+'; remote_connection_verified=false')
    return 0 if report['local_ready'] else 2


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError) as error:
        print('rdma_preflight: '+str(error),file=sys.stderr);sys.exit(1)
