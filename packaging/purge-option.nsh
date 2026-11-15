; Return 1 only for a complete /PURGE command-line argument. Reading the
; native command line avoids both prefix matches and NSIS string truncation.
; The caller owns the returned stack value; all registers are preserved.
!macro ZhiyiDefinePurgeOptionFunction NAME
Function ${NAME}
  Push $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  StrCpy $7 0
  System::Call 'kernel32::GetCommandLineW() p.r0'
  ${If} $0 != 0
    System::Call 'shell32::CommandLineToArgvW(p r0, *i .r1) p.r2'
    ${If} $2 != 0
      ; argv[0] is the uninstaller path, not an option.
      StrCpy $3 1
      zhiyi_purge_loop:
        IntCmp $3 $1 zhiyi_purge_done 0 zhiyi_purge_done
        IntOp $4 $3 * ${NSIS_PTR_SIZE}
        IntOp $4 $2 + $4
        System::Call '*$4(p .r5)'
        ; CSTR_EQUAL is 2. A failed comparison returns 0 and preserves data.
        System::Call 'kernel32::CompareStringOrdinal(p r5, i -1, w "/PURGE", i -1, i 1) i.r6'
        ${If} $6 == 2
          StrCpy $7 1
          Goto zhiyi_purge_done
        ${EndIf}
        IntOp $3 $3 + 1
        Goto zhiyi_purge_loop
      zhiyi_purge_done:
      System::Call 'kernel32::LocalFree(p r2) p.r6'
    ${EndIf}
  ${EndIf}
  StrCpy $0 $7
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Exch $0
FunctionEnd
!macroend
