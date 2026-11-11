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

O serviço escuta somente em `127.0.0.1:47613`. Mantenha-o rodando enquanto usar os botões do wallpaper. O Wallpaper Engine não inicia esse auxiliar. Para iniciá-lo com o Windows, coloque um atalho para `pythonw.exe -I C:\temp\pack-midia-control\src\pack_midia_control.py` na pasta de Inicialização (`shell:startup`).

Por padrão, são aceitos apenas os comandos `previous`, `play-pause` e `next`, enviados pelo wallpaper com origem local. O auxiliar não seleciona um aplicativo específico: o Windows encaminha as teclas ao player que controla a sessão de mídia global ativa.

## Uso de IA (rota `/usage`)

`GET /usage` devolve o consumo do mês corrente de Claude Code, Codex e outros agentes (custo estimado, tokens por agente e o bloco ativo de 5 h), para o card "USO DE IA" do wallpaper.

- Requer Node.js: o auxiliar executa `npx -y ccusage@20`, que lê os logs locais dos agentes (`~/.claude`, `~/.codex`, ...). Na primeira execução o npm baixa o pacote; a versão é fixada na major 20.
- A coleta roda em segundo plano e fica em cache por 2 minutos; enquanto a primeira coleta não termina, a resposta é `{"ok": false, "pending": true}`.
- Os valores de custo são **estimativas** a partir de tokens, não a cobrança oficial.
- Nada é enviado pelo auxiliar; a rota respeita a mesma lista de origens e o mesmo bind em loopback.

### Limites do plano (sessão de 5 h e semanal)

A resposta de `/usage` inclui `limits` com o percentual usado e o horário de reinício de cada janela, lidos apenas de arquivos locais (sem credenciais e sem rede):

- **Codex:** do último registro `rate_limits` nos logs de sessão em `~/.codex/sessions` (ou `$CODEX_HOME`). O valor é o do último evento gravado, então pode estar um pouco atrás do exibido pelo app.
- **Claude Code:** pela status line. Configure em `~/.claude/settings.json`:

  ```json
  {
    "statusLine": {
      "type": "command",
      "command": "C:/caminho/para/python.exe -I C:/temp/pack-midia-control/src/claude_statusline.py"
    }
  }
  ```

  O `claude_statusline.py` guarda só os percentuais e horários em `%LOCALAPPDATA%\pack-midia-control\claude-limits.json` e mostra `modelo | 5h X% · 7d Y%` na barra. Só atualiza enquanto uma sessão do Claude Code estiver aberta (o dado de `rate_limits` existe para contas com limites de plano e depois da primeira resposta da sessão).

## Verificações

```powershell
python -m unittest discover -s tests -v
```

Os testes injetam um emissor simulado; não pausam nem avançam a mídia do usuário.

## Versionamento

O projeto usa SemVer. A versão inicial do pacote é `0.1.0`; releases futuras podem usar tags `vX.Y.Z`. Mensagens de commit seguem Conventional Commits (por exemplo, `feat: ...`, `fix: ...`, `docs: ...`).
