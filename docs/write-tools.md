# Ferramentas de escrita

Implementação do fork studiodexx sob Apache-2.0. A licença e os avisos
originais são preservados. Nenhum teste real foi executado sem credenciais.

## Configuração

Copie a configuração para um arquivo local e habilite explicitamente:

```yaml
namespaces:
  customers: true
  search: true
  metadata: true
  write: true
```

Aponte GOOGLE_ADS_MCP_TOOLS_CONFIG para esse arquivo. Configure no ambiente
do processo, usando seu gerenciador de segredos:

- GOOGLE_ADS_CLIENT_ID
- GOOGLE_ADS_CLIENT_SECRET
- GOOGLE_ADS_REFRESH_TOKEN
- GOOGLE_ADS_DEVELOPER_TOKEN
- GOOGLE_ADS_LOGIN_CUSTOMER_ID (administrador, quando necessário)
- MAX_DAILY_BUDGET (valor positivo na moeda da conta, sem padrão)
- GOOGLE_ADS_WRITE_AUDIT_LOG (caminho gravável; padrão ~/.google_ads_mcp/write_audit.jsonl)
- GOOGLE_ADS_WRITE_ALLOWED_CUSTOMERS (IDs separados por vírgula, recomendado)

As ferramentas de escrita não usam arquivos de credenciais, ADC nem tokens
recebidos em chamadas MCP. As ferramentas de leitura mantêm a autenticação
original do upstream. Não coloque segredos no código, YAML ou no chat.

## Ferramentas

| Ferramenta MCP | Efeito |
| --- | --- |
| write_create_search_campaign | Orçamento exclusivo e campanha Search PAUSED, atomicamente |
| write_create_ad_group | Grupo SEARCH_STANDARD PAUSED |
| write_create_responsive_search_ad | Anúncio responsivo PAUSED |
| write_set_status | PAUSED ou ENABLED em campanha, grupo ou anúncio |
| write_update_campaign_budget | Altera orçamento, recusando orçamento usado por várias campanhas |

Todas aceitam dry_run=true para validação pela API sem mutação.
Ativar exige confirm="ATIVAR <resource_id>" após autorização explícita.
Para anúncio, resource_id é "<ad_group_id>~<ad_id>".
Nenhum recurso é ativado na criação. Orçamentos não possuem status PAUSED.

MAX_DAILY_BUDGET é obrigatório em todas as escritas. Limita o orçamento
médio diário por campanha, não a soma da conta nem o gasto efetivo de cada dia:
o Google pode gastar até duas vezes o orçamento médio em um dia.
Referência: https://support.google.com/google-ads/answer/6385083

Antes de cada escrita, customer.test_account é consultado. Contas reais são
bloqueadas por padrão. GOOGLE_ADS_WRITE_REQUIRE_TEST_ACCOUNT=false remove essa
trava; só configure após validar a integração em conta de teste.

O log JSONL contém início e resultado correlacionados por operation_id.
Falha ao gravar o início bloqueia a operação. Se houver queda após a chamada
API, um registro sem resultado indica resultado desconhecido: consulte a conta
antes de repetir, para evitar duplicação. O log registra parâmetros e conteúdo
dos anúncios; restrinja o acesso ao arquivo. Não passe segredos em textos ou URLs.

Campanhas nascem sem segmentação geográfica/idioma e sem palavras-chave.
Configure esses itens antes de ativar em produção.

## Validação em conta de teste

Crie uma conta cliente de teste:
https://developers.google.com/google-ads/api/docs/best-practices/test-accounts

Configure também GOOGLE_ADS_TEST_CUSTOMER_ID e execute da raiz:

```sh
.venv/bin/python -m scripts.test_write_account
```

O roteiro força conta de teste, valida a criação, cria campanha/grupo/anúncio,
altera orçamento, valida ENABLED com dry_run e confirma via consulta que todos
permaneceram PAUSED. Imprime os recursos criados e os mantém para inspeção.
Uma execução real de ENABLED fica para uma chamada com autorização explícita.

Testes locais, sem acesso à conta:

```sh
.venv/bin/python -m unittest discover -s tests -p '*_test.py'
```
