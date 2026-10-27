"""Проверка полноты production patch и совпадения с рабочими исходниками."""
from pathlib import Path
import subprocess
import tempfile

BASE = 'c8bea2642d0e95402f373af55584a3b98cd89417'
TRACKED = ['gui/include/controllermanager.h', 'gui/src/controllermanager.cpp', 'lib/src/session.c', 'lib/src/takion.c', 'lib/include/chiaki/feedbacksender.h', 'lib/src/feedbacksender.c',
           'lib/include/chiaki/streamconnection.h', 'lib/src/streamconnection.c', 'gui/CMakeLists.txt', 'gui/include/streamsession.h', 'gui/include/settings.h',
           'gui/src/main.cpp', 'gui/src/qml/StreamView.qml', 'gui/src/qmlbackend.cpp',
           'gui/src/qmlmainwindow.cpp', 'gui/include/qmlmainwindow.h', 'gui/src/streamsession.cpp']
NEW = ['gui/include/mkbheadsetaudio.h', 'gui/src/mkbheadsetaudio.mm', 'gui/include/mkbcameraprobe.h', 'gui/include/mkboutputpolicy.h', 'gui/include/mkbfeedbackhealth.h', 'gui/include/mkbstatuspublication.h', 'gui/include/mkbtargetwindow.h', 'gui/include/mkbdashboardcontroller.h', 'gui/include/mkbdashboard.h', 'gui/src/mkbdashboard.cpp', 'gui/include/mkbsessioninput.h', 'gui/include/mkbworker.h', 'gui/src/mkbworker.cpp', 'gui/include/mkbsubmissiongate.h',
       'gui/include/mkbworkertrace.h', 'gui/src/mkbworkertrace.cpp', 'gui/include/mkbworkertypes.h', 'gui/include/mkbpermissionmonitor.h', 'gui/include/mkbnativephasetrace.h', 'gui/include/mkbmousetrace.h', 'gui/src/mkbmousetrace.cpp',
       'gui/include/mkbmapper.h', 'gui/src/mkbmapper.cpp',
       'gui/include/macinputcapture.h', 'gui/src/macinputcapture.mm',
       'gui/include/mkbwindowinput.h', 'gui/include/mkbprofile.h',
       'gui/src/mkbprofile.cpp', 'gui/src/mkbprofilejson.cpp',
       'gui/include/mkbsetup.h', 'gui/include/mkbdiagnostics.h',
       'gui/include/mkbmouseinput.h', 'gui/include/mkbcapturelifecycle.h', 'gui/include/mkbhidreport.h',
       'gui/include/mkbcapturecoordinator.h']


def git(vendor, *args):
    return subprocess.check_output(['git', '-C', str(vendor), *args])


def changed_production(vendor, base=BASE):
    paths = set(git(vendor, 'diff', '--name-only', '-z', base).decode().split('\0'))
    paths.update(git(vendor, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0'))
    return {p for p in paths if p and (p.startswith(('gui/', 'lib/', 'cli/', 'cmake/'))
                                     or p == 'CMakeLists.txt')}


def check_inventory(vendor, names, base=BASE):
    if git(vendor, 'rev-parse', 'HEAD').decode().strip() != base:
        raise ValueError('Неверный upstream commit')
    changed = changed_production(vendor, base)
    if changed != set(names):
        raise ValueError(f'Production inventory не совпадает: пропущены {sorted(changed-set(names))}, '
                         f'лишние {sorted(set(names)-changed)}')
    if not changed:
        raise ValueError('Upstream не содержит изменений прототипа')


def verify_snapshot(vendor, patch, base=BASE):
    names = [row.split('\t', 2)[2] for row in
             subprocess.check_output(['git', 'apply', '--numstat', str(patch)], text=True).splitlines()]
    check_inventory(vendor, names, base)
    with tempfile.TemporaryDirectory(prefix='ps5-xim-sources-') as tmp:
        archive = subprocess.Popen(['git', '-C', str(vendor), 'archive', base], stdout=subprocess.PIPE)
        subprocess.run(['tar', '-x', '-C', tmp], stdin=archive.stdout, check=True)
        archive.stdout.close()
        if archive.wait():
            raise ValueError('Ошибка чтения upstream')
        # TMPDIR may be inside the project build tree; isolate Git path resolution.
        subprocess.run(['git', 'init', '--quiet', tmp], check=True)
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=tmp, check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=tmp, check=True)
        for name in names:
            expected, actual = Path(tmp)/name, Path(vendor)/name
            if expected.exists() != actual.exists() or (expected.exists() and expected.read_bytes()!=actual.read_bytes()):
                raise ValueError('Patch не совпадает с исходником: '+name)
    return len(names)
