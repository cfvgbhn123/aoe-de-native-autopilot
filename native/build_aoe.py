"""Build the x64 bridge without relying on VS registry registration."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'native'/'aoe_native_bridge.cpp'
DLL=ROOT/'build'/'aoe'/'aoe_native_v6.dll'
MANIFEST=DLL.with_suffix('.json')

def source_digest():
    return hashlib.sha256(SOURCE.read_bytes()+Path(__file__).read_bytes()).hexdigest()

def current_build():
    try:
        record=json.loads(MANIFEST.read_text(encoding='utf-8'))
        return record if record['source_sha256']==source_digest() and record['dll_sha256']==hashlib.sha256(DLL.read_bytes()).hexdigest() else None
    except (OSError,ValueError,KeyError):return None

def build():
    folders={Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)')),Path(os.environ.get('ProgramFiles',r'C:\Program Files'))}
    compilers=[]
    for folder in folders:
        compilers.extend(folder.glob('Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe'))
    if not compilers:raise RuntimeError('缺少 MSVC x64 C++ 编译工具，请安装 Visual Studio Build Tools 的 C++ 桌面组件')
    cl=sorted(compilers)[-1];msvc=cl.parents[3]
    sdks=[]
    for folder in folders:
        for inc in folder.glob('Windows Kits/10/Include/*'):
            if (inc/'um'/'Windows.h').exists() and (inc.parents[1]/'Lib'/inc.name/'um'/'x64'/'kernel32.lib').exists():sdks.append(inc)
    if not sdks:raise RuntimeError('缺少 Windows 10/11 SDK 头文件和 x64 库')
    sdk=sorted(sdks)[-1];lib=sdk.parents[1]/'Lib'/sdk.name
    digest=source_digest();build_id=int(digest[:8],16)
    DLL.parent.mkdir(parents=True,exist_ok=True)
    args=[str(cl),'/nologo','/LD','/MT','/O2','/W4','/WX','/EHsc','/std:c++17',f'/DAOE_BUILD_ID={build_id}u',
        f'/I{msvc/"include"}',*[f'/I{sdk/p}' for p in ('ucrt','shared','um')],str(SOURCE),
        f'/Fo{DLL.with_suffix(".obj")}',f'/Fe{DLL}','/link','/DYNAMICBASE','/NXCOMPAT',
        f'/LIBPATH:{msvc/"lib"/"x64"}',f'/LIBPATH:{lib/"ucrt"/"x64"}',f'/LIBPATH:{lib/"um"/"x64"}']
    env=os.environ.copy();env['PATH']=str(cl.parent)+os.pathsep+env.get('PATH','')
    result=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,text=True,errors='replace')
    print(result.stdout,end='');print(result.stderr,end='',file=sys.stderr)
    if result.returncode:raise RuntimeError(f'原生桥接编译失败 ({result.returncode})')
    exports=subprocess.run([str(cl.parent/'dumpbin.exe'),'/exports',str(DLL)],capture_output=True,text=True,errors='replace',env=env)
    required=('AoeInitialize','AoeRelease','AoeBuildId')
    if exports.returncode or any(name not in exports.stdout for name in required):raise RuntimeError('DLL 导出校验失败')
    record={'protocol':6,'source_sha256':digest,'dll_sha256':hashlib.sha256(DLL.read_bytes()).hexdigest(),'build_id':build_id,'exports':list(required)}
    MANIFEST.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print('Built and verified '+str(DLL))
    return record

if __name__=='__main__':
    try:build()
    except (OSError,RuntimeError) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
