# pack-midia-control

Auxiliar local para os controles de mídia do wallpaper NEON NOIR. No Windows, envia as teclas multimídia globais para a sessão de mídia ativa: faixa anterior, reproduzir/pausar e próxima faixa.

## Requisitos

- Windows 10/11
- Python 3.10 ou mais recente
- Nenhuma dependência de runtime de terceiros

## Executar

No PowerShell ou Windows Terminal:

```powershell
python C:\temp\pack-midia-control\src\pack_midia_control.py
```

O serviço escuta somente em `127.0.0.1:47613`. Mantenha-o rodando enquanto usar os botões do wallpaper. O Wallpaper Engine não inicia esse auxiliar e ele não está configurado para iniciar com o Windows.

Por padrão, são aceitos apenas os comandos `previous`, `play-pause` e `next`, enviados pelo wallpaper com origem local. O auxiliar não seleciona um aplicativo específico: o Windows encaminha as teclas ao player que controla a sessão de mídia global ativa.

## Verificações

```powershell
python -m unittest discover -s tests -v
```

Os testes injetam um emissor simulado; não pausam nem avançam a mídia do usuário.

## Versionamento

O projeto usa SemVer. A versão inicial do pacote é `0.1.0`; releases futuras podem usar tags `vX.Y.Z`. Mensagens de commit seguem Conventional Commits (por exemplo, `feat: ...`, `fix: ...`, `docs: ...`).
