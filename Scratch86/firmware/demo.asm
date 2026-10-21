# Scratch86 v0.1 demo boot image.  Assembled with GNU as, linked at 0000:7C00.
# Shows text-mode output through genuine x86 code: clears the screen, prints a
# banner and colour bars into video memory (B800:0000), then counts forever
# printing the counter in hex using only subtraction loops.
        xor ax, ax
        mov ds, ax
        mov ax, 0xB800
        mov es, ax
        # --- clear screen
        xor di, di
        mov cx, 2000
        mov ax, 0x0720
clear:  mov es:[di], ax
        add di, 2
        loop clear
        # --- banner (row 1, col 2) white on blue
        mov si, offset banner
        mov di, (1*80+2)*2
        mov ah, 0x1F
        call puts
        mov si, offset line2
        mov di, (3*80+2)*2
        mov ah, 0x07
        call puts
        mov si, offset line3
        mov di, (4*80+2)*2
        mov ah, 0x07
        call puts
        # --- 16 foreground colours (full blocks)
        mov di, (7*80+2)*2
        xor bx, bx
fgbars: mov ah, bl
        mov al, 0xDB
        mov es:[di], ax
        add di, 2
        mov es:[di], ax
        add di, 2
        inc bl
        cmp bl, 16
        jne fgbars
        # --- 8 background colours
        mov di, (9*80+2)*2
        xor bx, bx
bgbars: mov ah, bl
        add ah, ah
        add ah, ah
        add ah, ah
        add ah, ah
        add ah, 0x0F
        mov al, 'x'
        mov es:[di], ax
        add di, 2
        mov es:[di], ax
        add di, 2
        inc bl
        cmp bl, 8
        jne bgbars
        # --- label for the counter
        mov si, offset ctrlabel
        mov di, (12*80+2)*2
        mov ah, 0x0E
        call puts
        xor dx, dx
forever:
        inc dx
        mov si, offset places
        mov di, (12*80+12)*2
        mov ax, dx
digit:  mov bx, [si]
        xor cx, cx
sub16:  cmp ax, bx
        jb emit
        sub ax, bx
        inc cx
        jmp sub16
emit:   add cl, '0'
        cmp cl, '9'
        jbe put
        add cl, 7
put:    mov ch, 0x0A
        mov es:[di], cx
        add di, 2
        add si, 2
        cmp si, offset places + 8
        jne digit
        jmp forever
# --- puts: ds:si zero-terminated string -> es:di with attribute ah
puts:   mov al, [si]
        test al, al
        jz done
        mov es:[di], ax
        add di, 2
        inc si
        jmp puts
done:   ret
banner:   .asciz " SCRATCH86 V0.1 - x86 CPU EXECUTING INSIDE SCRATCH BLOCKS "
line2:    .asciz "This text was written to video memory by real x86 machine code."
line3:    .asciz "Open settings (gear, top-right) to enable diagnostic overlays."
ctrlabel: .asciz "COUNTER:"
places:   .word 0x1000, 0x0100, 0x0010, 0x0001
