"""Compile and execute the actual C++ bridge against a fake offline world."""
import os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
folders={Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)')),Path(os.environ.get('ProgramFiles',r'C:\Program Files'))}
cl=sorted(p for folder in folders for p in folder.glob('Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe'))[-1]
msvc=cl.parents[3]
sdk=sorted(p for folder in folders for p in folder.glob('Windows Kits/10/Include/*') if (p/'um'/'Windows.h').exists())[-1]
lib=sdk.parents[1]/'Lib'/sdk.name
out=root/'build'/'aoe'/'native_policy.exe';out.parent.mkdir(parents=True,exist_ok=True)
env=os.environ.copy();env['PATH']=str(cl.parent)+os.pathsep+env.get('PATH','')
args=[str(cl),'/nologo','/MT','/O2','/W4','/WX','/EHsc','/std:c++17',f'/I{msvc/"include"}',*[f'/I{sdk/p}' for p in ('ucrt','shared','um')],str(root/'tests'/'native_policy.cpp'),f'/Fo{out.with_suffix(".obj")}',f'/Fe{out}','/link',f'/LIBPATH:{msvc/"lib"/"x64"}',f'/LIBPATH:{lib/"ucrt"/"x64"}',f'/LIBPATH:{lib/"um"/"x64"}']
result=subprocess.run(args,cwd=root,env=env)
if result.returncode:sys.exit(result.returncode)
sys.exit(subprocess.run([str(out)],timeout=15).returncode)
