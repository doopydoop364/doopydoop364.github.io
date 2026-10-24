#!/bin/sh
# Arch86 Neovim 0.10 setup: built-in clangd LSP, Git, and terminal shortcuts.
set -eu
if [ "$(id -u)" -eq 0 ]; then
  echo "Run as your normal Arch86 user, not with sudo." >&2
  exit 1
fi
if ! command -v nvim >/dev/null 2>&1; then
  echo "Install Neovim first: sudo pacman -S neovim" >&2
  exit 1
fi
dir="$HOME/.config/nvim"
mkdir -p "$dir"
if [ -f "$dir/init.lua" ]; then
  backup="$dir/init.lua.bak.$(date +%Y%m%d-%H%M%S)"
  cp "$dir/init.lua" "$backup"
  echo "Backed up existing config: $backup"
fi
cat > "$dir/init.lua" <<'LUA'
-- Arch86 / Neovim 0.10 (no plugins or 64-bit binaries required)
vim.g.mapleader = " "
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.cursorline = true
vim.opt.signcolumn = "yes"
vim.opt.termguicolors = true
vim.opt.expandtab = true
vim.opt.shiftwidth = 4
vim.opt.tabstop = 4
vim.opt.smartindent = true
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.undofile = true
vim.opt.completeopt = "menuone,noselect"
vim.keymap.set("n", "<leader>e", vim.cmd.Explore)
vim.keymap.set("n", "<leader>t", "<cmd>terminal<CR>")
vim.keymap.set("n", "<leader>gg", function()
  if vim.fn.executable("lazygit") == 1 then
    vim.cmd("terminal lazygit")
  else
    vim.notify("lazygit isn't installed", vim.log.levels.WARN)
  end
end)
vim.keymap.set("n", "<leader>f", function()
  if vim.fn.executable("fzf") ~= 1 then
    vim.notify("Install fzf to use fuzzy file search", vim.log.levels.WARN)
    return
  end
  local choice = vim.fn.systemlist("rg --files --hidden -g '!.git' | fzf")[1]
  if choice and choice ~= "" then
    vim.cmd.edit(vim.fn.fnameescape(choice))
  end
end)
vim.diagnostic.config({ virtual_text = true, underline = true, signs = true })
vim.api.nvim_create_autocmd("FileType", {
  pattern = { "c", "cpp" },
  callback = function(args)
    if vim.fn.executable("clangd") ~= 1 then return end
    local file = vim.api.nvim_buf_get_name(args.buf)
    if file == "" then return end
    local root = vim.fs.root(file, { "compile_commands.json", "compile_flags.txt", ".git" })
      or vim.fn.fnamemodify(file, ":h")
    vim.lsp.start({
      name = "clangd",
      cmd = { "clangd" },
      root_dir = root,
    }, { bufnr = args.buf })
  end,
})
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(args)
    vim.bo[args.buf].omnifunc = "v:lua.vim.lsp.omnifunc"
    local opts = { buffer = args.buf }
    vim.keymap.set("n", "gd", vim.lsp.buf.definition, opts)
    vim.keymap.set("n", "K", vim.lsp.buf.hover, opts)
    vim.keymap.set("n", "<leader>rn", vim.lsp.buf.rename, opts)
    vim.keymap.set("n", "<leader>ca", vim.lsp.buf.code_action, opts)
  end,
})
LUA
echo "Installed $dir/init.lua"
if command -v clangd >/dev/null 2>&1; then
  echo "clangd: found"
else
  echo "clangd missing; install clang to enable C/C++ LSP."
fi
echo "Open a .c file with nvim; use :LspInfo, gd, K, Ctrl-X Ctrl-O."
