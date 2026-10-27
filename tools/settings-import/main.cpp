// SPDX-License-Identifier: LicenseRef-AGPL-3.0-only-OpenSSL
#include <QCoreApplication>
#include <QFile>
#include <QFileInfo>
#include <QMap>
#include <QSettings>
#include <QTemporaryDir>
#include <QTextStream>
#include <QVector>
#include <QUuid>

struct Store { QString source, target; QMap<QString,QVariant> values; };
static bool copySettings(const QString &sourceOrg, const QString &sourceApp,
                         const QString &targetOrg, const QString &targetApp, QSettings::Format format=QSettings::NativeFormat)
{
    QSettings source(format,QSettings::UserScope,sourceOrg,sourceApp); source.setFallbacksEnabled(false);
    if(source.allKeys().isEmpty() || source.status()!=QSettings::NoError) return false;
    QVector<Store> stores{{sourceApp,targetApp,{}},{"pl_render_params","pl_render_params",{}}};
    int count=source.beginReadArray("profiles");
    for(int i=0;i<count;++i) {
        source.setArrayIndex(i);
        QString profile=source.value("settings/profile_name").toString();
        if(!profile.isEmpty()) stores.push_back({sourceApp+"-"+profile,targetApp+"-"+profile,{}});
    }
    source.endArray();
    // Read and validate all destinations before writing any of them.
    for(auto &store:stores) {
        QSettings src(format,QSettings::UserScope,sourceOrg,store.source); src.setFallbacksEnabled(false);
        QSettings dst(format,QSettings::UserScope,targetOrg,store.target); dst.setFallbacksEnabled(false);
        if(QFileInfo(dst.fileName()).isSymLink() || !dst.isWritable()) return false;
        for(const auto &key:src.allKeys()) store.values.insert(key,src.value(key));
        if(src.status()!=QSettings::NoError || dst.status()!=QSettings::NoError) return false;
        // Import must never cause an automatic PS5 connection.
        store.values.remove("settings/auto_connect_mac");
        if(!dst.value("settings/auto_connect_mac").toByteArray().isEmpty()) return false;
        for(auto it=store.values.begin();it!=store.values.end();) {
            if(!dst.contains(it.key())) { ++it; continue; }
            if(dst.value(it.key())==it.value()) { it=store.values.erase(it); continue; }
            // Renderer preferences may be initialized by a startup smoke test.
            // Preserve them; this store never contains console registration.
            if(store.target=="pl_render_params") { it=store.values.erase(it); continue; }
            // An empty QSettings array is a default-created placeholder, not a registration.
            const QString group=it.key().left(it.key().size()-5);
            bool emptyArray=it.key().endsWith("/size") && dst.value(it.key()).toInt()==0;
            if(emptyArray) for(const auto &key:dst.allKeys())
                if(key.startsWith(group+"/") && key!=it.key()) emptyArray=false;
            if(!emptyArray) return false;
            ++it;
        }
    }
    for(const auto &store:stores) {
        QSettings dst(format,QSettings::UserScope,targetOrg,store.target); dst.setFallbacksEnabled(false);
        for(auto it=store.values.cbegin();it!=store.values.cend();++it) dst.setValue(it.key(),it.value());
        dst.sync();
        if(dst.status()!=QSettings::NoError) return false;
        if(QFileInfo::exists(dst.fileName()) &&
            !QFile::setPermissions(dst.fileName(),QFileDevice::ReadOwner|QFileDevice::WriteOwner)) return false;
    }
    return true;
}
static int selfTest(QSettings::Format format)
{
    QTemporaryDir temp;
    if(!temp.isValid()) return 1;
    if(format==QSettings::IniFormat)
        QSettings::setPath(format,QSettings::UserScope,temp.path());
    const QString id="MkbImportTest"+QUuid::createUuid().toString(QUuid::Id128);
    const QString sourceOrg=id+"Source", targetOrg=id+"Target";
    struct Cleanup {
        QSettings::Format format; QString sourceOrg,targetOrg;
        ~Cleanup() {
            for(const auto &org:{sourceOrg,targetOrg})
                for(const auto &app:{QString("Official"),QString("Prototype"),QString("pl_render_params"),
                                    QString("Official-test-profile"),QString("Prototype-test-profile")}) {
                    QSettings settings(format,QSettings::UserScope,org,app);
                    const auto path=settings.fileName();
                    settings.clear(); settings.sync(); QFile::remove(path);
                }
        }
    } cleanup{format,sourceOrg,targetOrg};
    QSettings src(format,QSettings::UserScope,sourceOrg,"Official"); src.setFallbacksEnabled(false);
    src.setValue("version",1);
    src.setValue("registered_hosts/size",1);
    src.setValue("registered_hosts/1/key","test-placeholder");
    src.setValue("settings/auto_connect_mac","test-auto-connect");
    src.beginWriteArray("profiles"); src.setArrayIndex(0);
    src.setValue("settings/profile_name","test-profile"); src.endArray(); src.sync();
    QMap<QString,QVariant> before;
    for(const auto &key:src.allKeys()) before.insert(key,src.value(key));
    QSettings originalProfile(format,QSettings::UserScope,sourceOrg,"Official-test-profile");
    originalProfile.setValue("registered_hosts/1/key","profile-placeholder"); originalProfile.sync();
    QSettings dst(format,QSettings::UserScope,targetOrg,"Prototype"); dst.setFallbacksEnabled(false);
    dst.setValue("version",1); dst.setValue("settings/local-only","preserved");
    dst.setValue("registered_hosts/size",0); dst.sync();
    QSettings sourceRender(format,QSettings::UserScope,sourceOrg,"pl_render_params");
    QSettings targetRender(format,QSettings::UserScope,targetOrg,"pl_render_params");
    sourceRender.setValue("placebo_settings/upscaler","source-renderer"); sourceRender.sync();
    targetRender.setValue("placebo_settings/upscaler","existing-renderer"); targetRender.sync();
    if(!copySettings(sourceOrg,"Official",targetOrg,"Prototype",format)) return 2;
    targetRender.sync();
    if(targetRender.value("placebo_settings/upscaler").toString()!="existing-renderer") return 12;
    dst.sync();
    if(dst.contains("settings/auto_connect_mac")) return 3;
    if(dst.value("registered_hosts/1/key").toString()!="test-placeholder") return 4;
    if(dst.value("settings/local-only").toString()!="preserved") return 5;
    QSettings profile(format,QSettings::UserScope,targetOrg,"Prototype-test-profile");
    if(profile.value("registered_hosts/1/key").toString()!="profile-placeholder") return 6;
    for(const auto &file:{dst.fileName(),targetRender.fileName(),profile.fileName()})
        if(!QFile::setPermissions(file,QFileDevice::ReadOwner|QFileDevice::WriteOwner|QFileDevice::ReadGroup|QFileDevice::ReadOther)) return 13;
    if(!copySettings(sourceOrg,"Official",targetOrg,"Prototype",format)) return 7;
    for(const auto &file:{dst.fileName(),targetRender.fileName(),profile.fileName()})
        if(QFile::permissions(file) & (QFileDevice::ReadGroup|QFileDevice::WriteGroup|QFileDevice::ExeGroup|
            QFileDevice::ReadOther|QFileDevice::WriteOther|QFileDevice::ExeOther)) return 14;
    dst.setValue("registered_hosts/1/key","existing-registration"); dst.sync();
    src.setValue("settings/new-missing-key","must-not-copy-on-conflict"); src.sync();
    if(copySettings(sourceOrg,"Official",targetOrg,"Prototype",format)) return 8;
    dst.sync();
    if(dst.value("registered_hosts/1/key").toString()!="existing-registration" || dst.contains("settings/new-missing-key")) return 9;
    src.remove("settings/new-missing-key"); src.sync();
    QMap<QString,QVariant> after;
    for(const auto &key:src.allKeys()) after.insert(key,src.value(key));
    if(before!=after || originalProfile.value("registered_hosts/1/key").toString()!="profile-placeholder") return 10;
    dst.setValue("registered_hosts/1/key","test-placeholder"); dst.setValue("version",2); dst.sync();
    if(copySettings(sourceOrg,"Official",targetOrg,"Prototype",format)) return 11;
    QTextStream(stdout) << "PASS " << (format==QSettings::NativeFormat ? "NativeFormat" : "IniFormat")
        << " defaults merge / profiles / source preserved / conflicts refused / own fixtures cleaned\n";
    return 0;
}
int main(int argc,char **argv)
{
    QCoreApplication app(argc,argv);
    if(app.arguments()==QStringList{app.arguments()[0],"--self-test"}) { int result=selfTest(QSettings::IniFormat); return result ? result : selfTest(QSettings::NativeFormat); }
    if(app.arguments()!=QStringList{app.arguments()[0],"--copy-official-settings"}) {
        QTextStream(stderr) << "Запускайте через scripts/import-settings.sh после закрытия обоих клиентов.\n"; return 1;
    }
    // No key names, values, account identifiers or paths are printed.
    if(!copySettings("Chiaki","Chiaki","ChiakiMkbPrototype","ChiakiMkbPrototype")) {
        QTextStream(stderr) << "Импорт не выполнен полностью. Проверьте исходные настройки и конфликты значений в namespace назначения. Существующие значения не перезаписываются.\n"; return 2;
    }
    QTextStream(stdout) << "Копия настроек импортирована. Автоподключение отключено.\n";
}
