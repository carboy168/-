#define MyAppName "工程规范智能体"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Engineering Norm Agent"
#define MyAppExeName "EngineeringNormAgent.exe"

[Setup]
AppId={{BE9F5858-0E7B-49AC-A113-5D2CBB83C279}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\EngineeringNormAgent
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=release
OutputBaseFilename=工程规范智能体_V1.0_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SourceDir={#SourcePath}\..
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
Source: "dist\EngineeringNormAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\EngineeringNormAgent\cache"

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if MsgBox('是否卸载工程规范智能体？项目数据库、规范全文、项目文件和备份将保留在本机 LocalAppData 中。', mbConfirmation, MB_YESNO) = IDNO then
    Result := False;
end;
