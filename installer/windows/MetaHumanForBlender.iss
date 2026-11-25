; Windows installer for the MetaHuman for Blender extension.
; Build with installer\build_installers.ps1, which passes the version and the extension zip:
;   ISCC.exe /DAppVersion=1.0.0 /DExtensionZip=<path to mfb-1.0.0.zip> /O<output dir> MetaHumanForBlender.iss
;
; The installer finds the Blender 4.2+ versions on this PC, lets the user pick which ones to
; install into, and runs each Blender's own extension installer:
;   blender.exe --command extension install-file -r user_default --enable <zip>
; The uninstaller runs "extension remove" in the same Blender versions.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef ExtensionZip
  #error Pass /DExtensionZip=<path to the extension zip>
#endif

#define AppName "MetaHuman for Blender"
#define ExtensionId "mfb"

[Setup]
AppId={{65D9A118-0B2C-4687-BCB2-721BED592FED}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Ken van Riel
AppPublisherURL=https://github.com/kvriel/MetaHumanForBlender
AppSupportURL=https://github.com/kvriel/MetaHumanForBlender/issues
AppUpdatesURL=https://github.com/kvriel/MetaHumanForBlender/releases
DefaultDirName={localappdata}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=MetaHuman-for-Blender-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={uninstallexe}
SetupLogging=yes

[Files]
Source: "{#ExtensionZip}"; DestDir: "{app}"; DestName: "{#ExtensionId}-{#AppVersion}.zip"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[UninstallDelete]
Type: files; Name: "{app}\blender_paths.txt"

[Code]
var
  BlenderPage: TInputOptionWizardPage;
  OtherPage: TInputFileWizardPage;
  BlenderPaths: TStringList;

{ ---- finding Blender ---------------------------------------------------- }

procedure AddBlender(const Exe, Version: String);
var
  I: Integer;
  Lbl: String;
begin
  if (Exe = '') or not FileExists(Exe) then
    Exit;
  for I := 0 to BlenderPaths.Count - 1 do
    if CompareText(BlenderPaths[I], Exe) = 0 then
      Exit;
  BlenderPaths.Add(Exe);
  if Version <> '' then
    Lbl := 'Blender ' + Version + '  (' + ExtractFileDir(Exe) + ')'
  else
    Lbl := ExtractFileDir(Exe);
  BlenderPage.AddEx(Lbl, 0, False);
  BlenderPage.Values[BlenderPage.CheckListBox.Items.Count - 1] := True;
end;

procedure ScanUninstallKeys(RootKey: Integer; const Path: String);
var
  Names: TArrayOfString;
  I: Integer;
  Key, DisplayName, Location, Version: String;
begin
  if not RegGetSubkeyNames(RootKey, Path, Names) then
    Exit;
  for I := 0 to GetArrayLength(Names) - 1 do
  begin
    Key := Path + '\' + Names[I];
    if RegQueryStringValue(RootKey, Key, 'DisplayName', DisplayName) and
       (Pos('blender', Lowercase(DisplayName)) = 1) and
       RegQueryStringValue(RootKey, Key, 'InstallLocation', Location) then
    begin
      if not RegQueryStringValue(RootKey, Key, 'DisplayVersion', Version) then
        Version := '';
      AddBlender(AddBackslash(Location) + 'blender.exe', Version);
    end;
  end;
end;

procedure ScanFolder(const Dir: String);
var
  Rec: TFindRec;
  Name, Version: String;
begin
  if FindFirst(AddBackslash(Dir) + '*', Rec) then
  try
    repeat
      if (Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and (Rec.Name <> '.') and (Rec.Name <> '..') then
      begin
        Name := Rec.Name;
        Version := '';
        if Pos('blender ', Lowercase(Name)) = 1 then
          Version := Copy(Name, 9, Length(Name));
        AddBlender(AddBackslash(Dir) + Name + '\blender.exe', Version);
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

procedure FindBlenders;
var
  Uninst: String;
begin
  Uninst := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall';
  ScanUninstallKeys(HKLM64, Uninst);
  ScanUninstallKeys(HKLM32, Uninst);
  ScanUninstallKeys(HKCU, Uninst);
  ScanFolder(ExpandConstant('{commonpf64}\Blender Foundation'));
  ScanFolder(ExpandConstant('{localappdata}\Programs\Blender Foundation'));
  AddBlender(ExpandConstant('{commonpf32}\Steam\steamapps\common\Blender\blender.exe'), 'Steam');
end;

{ ---- Blender must be closed --------------------------------------------- }

function BlenderRunning: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c tasklist /FI "IMAGENAME eq blender.exe" | find /I "blender.exe" >nul',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function WaitForBlenderToClose: Boolean;
begin
  Result := True;
  while BlenderRunning do
    if SuppressibleMsgBox('Blender is running. Close every Blender window, then click Retry.' + #13#10#13#10 +
                          'Blender would otherwise overwrite the change to its preferences when it quits.',
                          mbError, MB_RETRYCANCEL, IDCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
end;

{ ---- wizard -------------------------------------------------------------- }

procedure InitializeWizard;
begin
  BlenderPaths := TStringList.Create;
  BlenderPage := CreateInputOptionPage(wpWelcome, 'Choose Blender',
    'Which Blender versions should get {#AppName}?',
    'These Blender versions were found on this PC. {#AppName} needs Blender 4.2 or newer; ' +
    'an older version will report that it could not install.', False, False);
  FindBlenders;
  OtherPage := CreateInputFilePage(BlenderPage.ID, 'Another Blender',
    'Install into a Blender that is not listed?',
    'For a portable or unpacked Blender, pick its blender.exe. Leave this empty to skip.');
  OtherPage.Add('blender.exe:', 'Blender|blender.exe', '.exe');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  I: Integer;
  Any: Boolean;
begin
  Result := True;
  if CurPageID = OtherPage.ID then
  begin
    Any := (Trim(OtherPage.Values[0]) <> '');
    for I := 0 to BlenderPaths.Count - 1 do
      Any := Any or BlenderPage.Values[I];
    if not Any then
    begin
      SuppressibleMsgBox('Pick at least one Blender version, or the path to a blender.exe.', mbError, MB_OK, IDOK);
      Result := False;
    end
    else if (Trim(OtherPage.Values[0]) <> '') and not FileExists(OtherPage.Values[0]) then
    begin
      SuppressibleMsgBox('That blender.exe does not exist.', mbError, MB_OK, IDOK);
      Result := False;
    end;
  end
  else if CurPageID = wpReady then
    Result := WaitForBlenderToClose;
end;

function RunBlender(const Exe, Params: String): Integer;
begin
  if not Exec(Exe, Params, ExtractFileDir(Exe), SW_HIDE, ewWaitUntilTerminated, Result) then
    Result := -1;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I, Code: Integer;
  Exe, Zip, Failed: String;
  Chosen, Done: TStringList;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Zip := ExpandConstant('{app}\{#ExtensionId}-{#AppVersion}.zip');
  Chosen := TStringList.Create;
  Done := TStringList.Create;
  try
    for I := 0 to BlenderPaths.Count - 1 do
      if BlenderPage.Values[I] then
        Chosen.Add(BlenderPaths[I]);
    if Trim(OtherPage.Values[0]) <> '' then
      Chosen.Add(Trim(OtherPage.Values[0]));
    Failed := '';
    for I := 0 to Chosen.Count - 1 do
    begin
      Exe := Chosen[I];
      WizardForm.StatusLabel.Caption := 'Installing into ' + ExtractFileDir(Exe) + '...';
      Code := RunBlender(Exe, '--command extension install-file -r user_default --enable "' + Zip + '"');
      Log(Format('%s: exit code %d', [Exe, Code]));
      if Code = 0 then
        Done.Add(Exe)
      else
        Failed := Failed + #13#10 + '  ' + ExtractFileDir(Exe);
    end;
    Done.SaveToFile(ExpandConstant('{app}\blender_paths.txt'));
    if Failed <> '' then
      SuppressibleMsgBox('{#AppName} could not be installed into:' + Failed + #13#10#13#10 +
        'These need Blender 4.2 or newer. You can still install it there by hand: in Blender, ' +
        'Edit > Preferences > Get Extensions > Install from Disk, and pick' + #13#10 + Zip,
        mbError, MB_OK, IDOK);
  finally
    Chosen.Free;
    Done.Free;
  end;
end;

{ ---- uninstall ----------------------------------------------------------- }

function InitializeUninstall: Boolean;
begin
  Result := True;
  while BlenderRunning do
    if SuppressibleMsgBox('Blender is running. Close every Blender window, then click Retry.',
                          mbError, MB_RETRYCANCEL, IDCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Paths: TArrayOfString;
  I, Code: Integer;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  if not LoadStringsFromFile(ExpandConstant('{app}\blender_paths.txt'), Paths) then
    Exit;
  for I := 0 to GetArrayLength(Paths) - 1 do
    if FileExists(Paths[I]) then
    begin
      Code := RunBlender(Paths[I], '--command extension remove {#ExtensionId}');
      Log(Format('remove from %s: exit code %d', [Paths[I], Code]));
    end;
end;
