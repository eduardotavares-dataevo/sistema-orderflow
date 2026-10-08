import requests
import json
import re
from datetime import datetime
# pyrefly: ignore [missing-import]
import urllib3

# Desabilita avisos de certificado SSL para conexões de desenvolvimento
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class SankhyaAPI:
    def __init__(self, base_url, usuario, senha):
        """
        :param base_url: Exemplo 'https://autogiro.nuvemdatacom.com.br:8034'
        """
        base = base_url.strip().rstrip('/')
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"https://{base}"
        elif base.startswith("http://") and not base.startswith("http://127.0.0.1") and not base.startswith("http://localhost"):
            # Converte para https caso seja servidor externo que exige SSL
            base = base.replace("http://", "https://", 1)
        self.base_url = base
        self.usuario = usuario
        self.senha = senha
        self.jsessionid = None
        self.session = requests.Session()
        self.session.verify = False
        self._cache_tsipar_texto = {}

    def autenticar(self):
        """Autentica na API do Sankhya e guarda o jsessionid"""
        url = f"{self.base_url}/mge/service.sbr?serviceName=MobileLoginSP.login&outputType=json"
        
        payload = {
            "serviceName": "MobileLoginSP.login",
            "requestBody": {
                "NOMUSU": {"$": self.usuario},
                "INTERNO": {"$": self.senha},
                "KEEPCONNECTED": {"$": "S"}
            }
        }

        try:
            response = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            if response.status_code == 200:
                dados = response.json()
                if dados.get("status") == "1":
                    self.jsessionid = dados["responseBody"]["jsessionid"]["$"]
                    print(f"[Sankhya] Autenticado com sucesso! Session: {self.jsessionid}")
                    return True
                else:
                    print(f"[Sankhya] Erro no login: {dados.get('statusMessage')}")
                    return False
            else:
                print(f"[Sankhya] Falha de conexão: Status {response.status_code}")
                return False
        except Exception as e:
            print(f"[Sankhya] Erro ao conectar no servidor: {e}")
            return False

    def _executar_sql(self, sql):
        """Executa consulta SQL no DbExplorerSP com re-autenticação automática se a sessão expirar"""
        if not self.jsessionid:
            if not self.autenticar():
                return None

        url = f"{self.base_url}/mge/service.sbr?serviceName=DbExplorerSP.executeQuery&outputType=json&mgeSession={self.jsessionid}"
        try:
            r = self.session.post(url, json={"serviceName": "DbExplorerSP.executeQuery", "requestBody": {"sql": sql}}, headers={"Content-Type": "application/json"}, timeout=30)
            if r.status_code == 200:
                dados = r.json()
                if dados.get("status") == "1":
                    return dados
                elif dados.get("status") == "3" or "Sessão" in str(dados.get("statusMessage", "")) or "expirada" in str(dados.get("statusMessage", "")):
                    print("[Sankhya] Sessão expirada detectada. Reautenticando...")
                    if self.autenticar():
                        url_retry = f"{self.base_url}/mge/service.sbr?serviceName=DbExplorerSP.executeQuery&outputType=json&mgeSession={self.jsessionid}"
                        r_retry = self.session.post(url_retry, json={"serviceName": "DbExplorerSP.executeQuery", "requestBody": {"sql": sql}}, headers={"Content-Type": "application/json"}, timeout=30)
                        if r_retry.status_code == 200:
                            dados_retry = r_retry.json()
                            if dados_retry.get("status") == "1":
                                return dados_retry
                print(f"[Sankhya] Erro na consulta SQL: {dados.get('statusMessage')}")
        except Exception as e:
            print(f"[Sankhya] Exceção ao executar SQL: {e}")
        return None


    def obter_parametro_tiptabpreco(self):
        """
        Consulta o parâmetro TIPTABPRECO (ou TIPTABPRECOS) na tabela TSIPAR.
        Retorna o nome amigável do tipo de tabela de preço configurado na base do cliente.
        """
        sql = "SELECT CHAVE, TEXTO, INTEIRO FROM TSIPAR WHERE CHAVE IN ('TIPTABPRECO', 'TIPTABPRECOS')"
        dados = self._executar_sql(sql)
        tiptab_texto = "Parceiro"
        opcoes = [
            "Única",
            "Região do Vendedor",
            "Região do Parceiro",
            "Perfil",
            "Parceiro",
            "Tipo de Negoc.",
            "Local",
            "Tipo de Negoc./Vendedor",
            "Parceiro/Tipo Negoc./Vendedor",
            "Parceiro/Empresa"
        ]

        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                if r[2] is not None:
                    idx = int(r[2])
                    if 0 <= idx < len(opcoes):
                        tiptab_texto = opcoes[idx]
                        break
                elif r[1] and str(r[1]).strip() and "\n" not in str(r[1]):
                    tiptab_texto = str(r[1]).strip()
                    break
        return tiptab_texto

    def obter_codtab_cliente(self, codparc=0, codemp=1, codvend=0, codreg=0, codcid=0, codbai=0, codtipparc=0, codtipvenda=0, codlocal=0):
        """
        Busca a tabela de preço (CODTAB) do cliente de acordo com o parâmetro TIPTABPRECO do Sankhya.
        Regras implementadas:
        - Única: 0
        - Região do Vendedor: TGFRGV -> CODTAB
        - Região do Parceiro: TGFREG -> CODTAB
        - Perfil: TGFPER -> CODTAB
        - Parceiro: TGFPAR -> CODTAB
        - Tipo de Negoc.: TGFTPV -> CODTAB
        - Local: TGFLOC -> CODTAB
        - Tipo de Negoc./Vendedor: TGFTPV/TGFVEN -> CODTAB
        - Parceiro/Tipo Negoc./Vendedor: Busca combinada (TGFPAR / TGFTPV / TGFVEN) -> CODTAB
        - Parceiro/Empresa: TGFPAR / TSIEMP -> CODTAB
        """
        tipo_param = self.obter_parametro_tiptabpreco()
        tipo_norm = tipo_param.upper().replace(" ", "").replace("/", "").replace(".", "").replace("Á", "A").replace("Ã", "A").replace("Ç", "C")

        # 1. Tabela Única
        if "UNICA" in tipo_norm:
            return 0

        # 2. Região do Vendedor (CODVEND)
        if "REGIAODOVENDEDOR" in tipo_norm or ("REGIAO" in tipo_norm and "VENDEDOR" in tipo_norm and "PARCEIRO" not in tipo_norm):
            if codvend:
                sql = f"SELECT NVL(RGV.CODTAB, 0) FROM TGFVEN VEN INNER JOIN TGFRGV RGV ON VEN.CODRGV = RGV.CODRGV WHERE VEN.CODVEND = {codvend}"
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 3. Região do Parceiro (CODPARC, CODREG, CODCID, CODBAI)
        if "REGIAODOPARCEIRO" in tipo_norm or ("REGIAO" in tipo_norm and "PARCEIRO" in tipo_norm):
            if codparc:
                sql = f"SELECT NVL(CODTAB, 0) FROM TGFPAR WHERE CODPARC = {codparc}"
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 4. Perfil (Parceiro -> TGFPAR.CODTIPPARC -> TGFTPP.CODTAB)
        if "PERFIL" in tipo_norm:
            if codparc:
                sql = f"""
                SELECT NVL(TPP.CODTAB, 0)
                FROM TGFPAR PAR
                INNER JOIN TGFTPP TPP ON PAR.CODTIPPARC = TPP.CODTIPPARC
                WHERE PAR.CODPARC = {codparc}
                """
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 5. Parceiro (CODPARC)
        if tipo_norm == "PARCEIRO" or ("PARCEIRO" in tipo_norm and "TIPO" not in tipo_norm and "EMPRESA" not in tipo_norm and "REGIAO" not in tipo_norm):
            if codparc:
                sql = f"SELECT NVL(CODTAB, 0) FROM TGFPAR WHERE CODPARC = {codparc}"
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 6. Tipo de Negoc. (CODTIPVENDA)
        if tipo_norm in ("TIPODENEGOC", "TIPODENEGOCIAÇÃO", "TIPONEGOC") or (("TIPONEGOC" in tipo_norm or "NEGOC" in tipo_norm) and "VENDEDOR" not in tipo_norm and "PARCEIRO" not in tipo_norm):
            if codtipvenda:
                sql = f"SELECT NVL(CODTAB, 0) FROM TGFTPV WHERE CODTIPVENDA = {codtipvenda}"
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 7. Local (CODLOCAL)
        if "LOCAL" in tipo_norm and "PARCEIRO" not in tipo_norm and "NEGOC" not in tipo_norm:
            if codlocal:
                sql = f"SELECT NVL(CODTAB, 0) FROM TGFLOC WHERE CODLOCAL = {codlocal}"
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 8. Tipo de Negoc./Vendedor (CODTIPVENDA, CODVEND)
        if "TIPODENEGOCVENDEDOR" in tipo_norm or ("NEGOC" in tipo_norm and "VENDEDOR" in tipo_norm and "PARCEIRO" not in tipo_norm):
            if codtipvenda or codvend:
                sql = f"""
                SELECT NVL(TPV.CODTAB, 0)
                FROM TGFTPV TPV
                LEFT JOIN TGFVEN VEN ON 1=1
                WHERE (TPV.CODTIPVENDA = {codtipvenda if codtipvenda else 0} OR VEN.CODVEND = {codvend if codvend else 0})
                ORDER BY CASE WHEN TPV.CODTIPVENDA = {codtipvenda if codtipvenda else 0} THEN 1 ELSE 2 END
                """
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 9. Parceiro/Tipo Negoc./Vendedor (CODPARC, CODTIPVENDA, CODVEND)
        if "PARCEIRO" in tipo_norm and "NEGOC" in tipo_norm and "VENDEDOR" in tipo_norm:
            if codparc:
                sql = f"SELECT NVL(CODTAB, 0) FROM TGFPAR WHERE CODPARC = {codparc}"
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # 10. Parceiro/Empresa (CODPARC, CODEMP)
        if "PARCEIROEMPRESA" in tipo_norm or ("PARCEIRO" in tipo_norm and "EMPRESA" in tipo_norm):
            if codparc:
                sql = f"SELECT NVL(CODTAB, 0) FROM TGFPAR WHERE CODPARC = {codparc}"
                res = self._executar_sql(sql)
                if res and res.get("status") == "1":
                    rows = res.get("responseBody", {}).get("rows", [])
                    if rows and rows[0][0] is not None:
                        return int(rows[0][0])
            return None

        # Fallback genérico para Parceiro caso codparc tenha sido informado
        if codparc:
            sql = f"SELECT NVL(CODTAB, 0) FROM TGFPAR WHERE CODPARC = {codparc}"
            res = self._executar_sql(sql)
            if res and res.get("status") == "1":
                rows = res.get("responseBody", {}).get("rows", [])
                if rows and rows[0][0] is not None:
                    return int(rows[0][0])

        return None

    def listar_produtos(self, pagina=1, itens_por_pagina=10, busca=None, codemp=1, codparc=0, codvend=0, codreg=0, codcid=0, codbai=0, codtipparc=0, codtipvenda=0, codlocal=0):
        """
        Consulta os produtos no Sankhya paginados com filtro por nome, marca, referência ou código.
        Gera as duas colunas de preço:
        - PRECO: SNK_PRECO(0, CODPROD)
        - PRECO_CLIENTE: SNK_PRECO(CODTAB_CLIENTE, CODPROD) ou 0 se a tabela de preço do cliente não estiver definida
        """
        codemp = int(codemp) if codemp else 1
        codtab_cliente = self.obter_codtab_cliente(
            codparc=codparc, 
            codemp=codemp, 
            codvend=codvend, 
            codreg=codreg, 
            codcid=codcid, 
            codbai=codbai, 
            codtipparc=codtipparc, 
            codtipvenda=codtipvenda, 
            codlocal=codlocal
        )

        if codtab_cliente is not None and codtab_cliente >= 0:
            preco_cliente_sql = f"NVL(SNK_PRECO({codtab_cliente}, P.CODPROD), 0)"
        else:
            preco_cliente_sql = "0"

        filtro_sql = "WHERE NVL(P.ATIVO, 'S') = 'S' AND P.USOPROD IN ('R', 'V')"
        if busca:
            termo_limpo = str(busca).replace("'", "''").upper()
            filtro_sql += f""" AND (
                UPPER(P.DESCRPROD) LIKE '%{termo_limpo}%'
                OR UPPER(NVL(P.MARCA, ' ')) LIKE '%{termo_limpo}%'
                OR UPPER(NVL(P.REFFORN, ' ')) LIKE '%{termo_limpo}%'
                OR UPPER(NVL(P.REFERENCIA, ' ')) LIKE '%{termo_limpo}%'
                OR TO_CHAR(P.CODGRUPOPROD) LIKE '%{termo_limpo}%'
                OR TO_CHAR(P.CODPROD) LIKE '%{termo_limpo}%'
            )"""

        # 1. Consulta o total de registros para cálculo das páginas
        sql_count = f"SELECT COUNT(1) AS TOTAL FROM TGFPRO P {filtro_sql}"
        dados_count = self._executar_sql(sql_count)
        total_registros = 0
        if dados_count and dados_count.get("status") == "1":
            total_registros = int(dados_count["responseBody"]["rows"][0][0])

        total_paginas = (total_registros + itens_por_pagina - 1) // itens_por_pagina if total_registros > 0 else 1
        if pagina > total_paginas:
            pagina = total_paginas
        if pagina < 1:
            pagina = 1

        offset_inicio = (pagina - 1) * itens_por_pagina + 1
        offset_fim = pagina * itens_por_pagina

        # 2. Consulta paginada trazendo PRECO (SNK_PRECO(0, P.CODPROD)) e PRECO_CLIENTE ({preco_cliente_sql})
        sql_paginado = f"""
        SELECT * FROM (
            SELECT 
                P.CODPROD, 
                P.DESCRPROD, 
                NVL(P.REFFORN, '-') AS REFFORN,
                NVL(P.MARCA, '-') AS MARCA,
                NVL(P.CODVOL, 'UN') AS CODVOL,
                NVL(SNK_PRECO(0, P.CODPROD), NVL((SELECT MAX(E.VLRVENDA) FROM TGFEXC E WHERE E.CODPROD = P.CODPROD), 0)) AS PRECO,
                {preco_cliente_sql} AS PRECO_CLIENTE,
                NVL((SELECT NVL(SUM(EST.ESTOQUE - EST.RESERVADO), 0) FROM TGFEST EST WHERE EST.CODPROD = P.CODPROD AND EST.CODEMP = {codemp} AND EST.CODLOCAL = 1000000), 0) AS ESTOQUE,
                NVL(P.REFERENCIA, '') AS REFERENCIA,
                ROW_NUMBER() OVER (ORDER BY P.CODPROD) AS RNUM
            FROM 
                TGFPRO P 
            {filtro_sql}
        ) WHERE RNUM BETWEEN {offset_inicio} AND {offset_fim}
        """

        produtos = []
        dados_prod = self._executar_sql(sql_paginado)
        if dados_prod and dados_prod.get("status") == "1":
            rows = dados_prod.get("responseBody", {}).get("rows", [])
            for row in rows:
                codvol_val = str(row[4]).strip() if row[4] else "UN"
                preco_val = float(row[5]) if row[5] else 0.0
                preco_cli_val = float(row[6]) if row[6] else 0.0
                est_val = int(row[7]) if row[7] else 0
                ref_val = str(row[8]).strip() if row[8] else ""
                
                # Consulta promoção do produto via parâmetro CONSPROMPORTPED da TSIPAR
                promo_val = self.obter_promocao_produto(codprod=row[0], codemp=codemp, codparc=codparc)

                produtos.append({
                    "id": row[0],
                    "nome": row[1],
                    "refforn": row[2],
                    "marca": row[3],
                    "codvol": codvol_val,
                    "preco": round(preco_val, 2),
                    "preco_cliente": round(preco_cli_val, 2),
                    "promocao": round(promo_val, 2),
                    "estoque": est_val,
                    "estoque_jp": est_val,
                    "estoque_cg": 0,
                    "referencia": ref_val,
                    "ativo": "S"
                })

        return produtos, total_registros, total_paginas

    def listar_produtos_alternativos(self, codprod, codemp=1, codparc=0):
        """
        Consulta produtos alternativos (TGFPAL) para o produto informado com estoque, preço, descrição e referência.
        Baseado na query:
        SELECT
            PAL.CODPROD,
            PAL.CODPRODALT,
            PRO.DESCRPROD,
            NVL(PRO.REFERENCIA, '-') AS REFERENCIA,
            NVL(SNK_PRECO(0,PAL.CODPRODALT), 0) AS PRECO,
            NVL((SELECT SUM(ESTOQUE - RESERVADO) FROM TGFEST WHERE CODEMP = :codemp AND CODPROD = PAL.CODPRODALT AND CODLOCAL = 1000000),0) AS ESTOQUE
        FROM
            TGFPAL PAL
            INNER JOIN TGFPRO PRO ON PAL.CODPRODALT = PRO.CODPROD
        WHERE
            PAL.CODPROD = :codprod
        """
        try:
            codprod = int(codprod)
            codemp = int(codemp) if codemp else 1
            codparc = int(codparc) if codparc else 0
        except (ValueError, TypeError):
            return []

        sql = f"""
        SELECT
            PAL.CODPROD,
            PAL.CODPRODALT,
            PRO.DESCRPROD,
            NVL(PRO.REFERENCIA, '-') AS REFERENCIA,
            NVL(PRO.REFFORN, '-') AS REFFORN,
            NVL(SNK_PRECO(0, PAL.CODPRODALT), NVL((SELECT MAX(E.VLRVENDA) FROM TGFEXC E WHERE E.CODPROD = PAL.CODPRODALT), 0)) AS PRECO,
            NVL((SELECT SUM(ESTOQUE - RESERVADO) FROM TGFEST WHERE CODEMP = {codemp} AND CODPROD = PAL.CODPRODALT AND CODLOCAL = 1000000), 0) AS ESTOQUE
        FROM
            TGFPAL PAL
            INNER JOIN TGFPRO PRO ON PAL.CODPRODALT = PRO.CODPROD
        WHERE
            PAL.CODPROD = {codprod}
        ORDER BY
            ESTOQUE DESC, PRO.DESCRPROD ASC
        """
        dados = self._executar_sql(sql)
        alternativos = []
        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                preco_val = float(r[5]) if r[5] else 0.0
                est_val = int(r[6]) if r[6] else 0
                promo_val = self.obter_promocao_produto(codprod=r[1], codemp=codemp, codparc=codparc)
                alternativos.append({
                    "codprod_orig": r[0],
                    "codprod_alt": r[1],
                    "descrprod": str(r[2]).strip() if r[2] else "",
                    "referencia": str(r[3]).strip() if r[3] else "-",
                    "refforn": str(r[4]).strip() if r[4] else "-",
                    "preco": round(preco_val, 2),
                    "promocao": round(promo_val, 2),
                    "estoque": est_val
                })
        return alternativos

    def obter_estoque_detalhado_tgfest(self, codprod):
        """
        Retorna as informações detalhadas de estoque da tabela TGFEST para o produto informado.
        Campos: CODEMP, CODLOCAL, ESTOQUE, RESERVADO, DISPONIVEL (ESTOQUE - RESERVADO), CONTROLE
        """
        try:
            codprod = int(codprod)
        except (ValueError, TypeError):
            return []

        sql = f"""
        SELECT 
            CODEMP, 
            CODLOCAL, 
            ESTOQUE, 
            RESERVADO, 
            (ESTOQUE - RESERVADO) AS DISPONIVEL, 
            NVL(CONTROLE, ' ') AS CONTROLE
        FROM TGFEST 
        WHERE CODPROD = {codprod}
        ORDER BY CODEMP, CODLOCAL
        """
        dados = self._executar_sql(sql)
        estoque_detalhado = []
        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                codemp_val = int(r[0]) if r[0] is not None else 0
                codlocal_val = int(r[1]) if r[1] is not None else 0
                est_val = float(r[2]) if r[2] is not None else 0.0
                res_val = float(r[3]) if r[3] is not None else 0.0
                disp_val = float(r[4]) if r[4] is not None else 0.0
                ctrl_val = str(r[5]).strip() if r[5] is not None else ""

                estoque_detalhado.append({
                    "codemp": codemp_val,
                    "codlocal": codlocal_val,
                    "estoque": est_val,
                    "reserva": res_val,
                    "disponivel": disp_val,
                    "controle": ctrl_val
                })
        return estoque_detalhado

    def listar_produtos_sugeridos(self, codprod, codemp=1, codparc=0):
        """
        Retorna a lista de produtos sugeridos / venda cruzada (TGFVCS) para o produto informado.
        """
        if not self.jsessionid:
            self.autenticar()

        try:
            codprod = int(codprod)
            codemp = int(codemp) if codemp else 1
            codparc = int(codparc) if codparc else 0
        except (ValueError, TypeError):
            return []

        sql = f"""
        SELECT
            PAL.CODORIG,
            PAL.CODPRODSUG,
            PRO.DESCRPROD,
            NVL(PRO.REFERENCIA, '-') AS REFERENCIA,
            NVL(PRO.REFFORN, '-') AS REFFORN,
            NVL(SNK_PRECO(0, PAL.CODPRODSUG), NVL((SELECT MAX(E.VLRVENDA) FROM TGFEXC E WHERE E.CODPROD = PAL.CODPRODSUG), 0)) AS PRECO,
            NVL((SELECT SUM(ESTOQUE - RESERVADO) FROM TGFEST WHERE CODEMP = {codemp} AND CODPROD = PAL.CODPRODSUG AND CODLOCAL = 1000000), 0) AS ESTOQUE
        FROM
            TGFVCS PAL
            INNER JOIN TGFPRO PRO ON PAL.CODPRODSUG = PRO.CODPROD
        WHERE
            PAL.CODORIG = {codprod}
        ORDER BY
            ESTOQUE DESC, PRO.DESCRPROD ASC
        """
        dados = self._executar_sql(sql)
        sugeridos = []
        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                preco_val = float(r[5]) if r[5] else 0.0
                est_val = int(r[6]) if r[6] else 0
                promo_val = self.obter_promocao_produto(codprod=r[1], codemp=codemp, codparc=codparc)
                sugeridos.append({
                    "codprod_orig": r[0],
                    "codprod_sug": r[1],
                    "descrprod": str(r[2]).strip() if r[2] else "",
                    "referencia": str(r[3]).strip() if r[3] else "-",
                    "refforn": str(r[4]).strip() if r[4] else "-",
                    "preco": round(preco_val, 2),
                    "promocao": round(promo_val, 2),
                    "estoque": est_val
                })
        return sugeridos

    def listar_pedidos_vendas(self, codvend=None, codemp=None, dt_ini=None, dt_fim=None, nunota=None, numnota=None, codparc=None, codprod=None, pagina=1, itens_por_pagina=15):
        """
        Consulta pedidos e notas em TGFCAB com filtros por Vendedor, Período, NUNOTA, Parceiro, Empresa.
        Retorna: (pedidos, total_registros, total_paginas, pagina_atual)
        """
        if not self.jsessionid:
            self.autenticar()

        # Apenas Pedidos de Venda (TGFCAB.TIPMOV = 'P')
        condicoes = ["CAB.TIPMOV = 'P'"]
        
        if codvend:
            try:
                cv = int(codvend)
                if cv > 0:
                    condicoes.append(f"CAB.CODVEND = {cv}")
            except (ValueError, TypeError):
                pass

        if codemp:
            try:
                ce = int(codemp)
                if ce > 0:
                    condicoes.append(f"CAB.CODEMP = {ce}")
            except (ValueError, TypeError):
                pass

        if nunota:
            try:
                nu = int(nunota)
                if nu > 0:
                    condicoes.append(f"CAB.NUNOTA = {nu}")
            except (ValueError, TypeError):
                pass

        if numnota:
            try:
                num = int(numnota)
                if num > 0:
                    condicoes.append(f"CAB.NUMNOTA = {num}")
            except (ValueError, TypeError):
                pass

        if codparc:
            try:
                cp = int(codparc)
                if cp > 0:
                    condicoes.append(f"CAB.CODPARC = {cp}")
            except (ValueError, TypeError):
                pass

        if dt_ini and dt_ini.strip():
            condicoes.append(f"TRUNC(CAB.DTNEG) >= TO_DATE('{dt_ini.strip()}', 'YYYY-MM-DD')")

        if dt_fim and dt_fim.strip():
            condicoes.append(f"TRUNC(CAB.DTNEG) <= TO_DATE('{dt_fim.strip()}', 'YYYY-MM-DD')")

        if codprod:
            try:
                cprod = int(codprod)
                if cprod > 0:
                    condicoes.append(f"EXISTS (SELECT 1 FROM TGFITE I WHERE I.NUNOTA = CAB.NUNOTA AND I.CODPROD = {cprod})")
            except (ValueError, TypeError):
                pass

        where_clause = " AND ".join(condicoes)

        # 1. Total de registros para cálculo de páginas
        sql_count = f"SELECT COUNT(1) AS TOTAL FROM TGFCAB CAB WHERE {where_clause}"
        dados_count = self._executar_sql(sql_count)
        total_registros = 0
        if dados_count and dados_count.get("status") == "1":
            total_registros = int(dados_count["responseBody"]["rows"][0][0])

        itens_por_pagina = int(itens_por_pagina) if itens_por_pagina else 15
        total_paginas = (total_registros + itens_por_pagina - 1) // itens_por_pagina if total_registros > 0 else 1
        
        pagina = int(pagina) if pagina else 1
        if pagina > total_paginas:
            pagina = total_paginas
        if pagina < 1:
            pagina = 1

        offset_inicio = (pagina - 1) * itens_por_pagina + 1
        offset_fim = pagina * itens_por_pagina

        # 2. Consulta paginada com ROW_NUMBER()
        sql = f"""
        SELECT * FROM (
            SELECT
                ROW_NUMBER() OVER (ORDER BY CAB.DTNEG DESC, CAB.NUNOTA DESC) AS RNUM,
                CAB.NUNOTA,
                CAB.NUMNOTA,
                NVL(CAB.STATUSNOTA, 'P') AS STATUSNOTA,
                NVL(CAB.LIBCONF, 'N') AS LIBCONF,
                CAB.CODPARC,
                NVL(PAR.RAZAOSOCIAL, 'CONSUMIDOR') AS NOMEPARC,
                NVL(CAB.VLRNOTA, 0) AS VLRNOTA,
                TO_CHAR(CAB.DTNEG, 'DD/MM/YYYY') AS DTNEG,
                CAB.CODEMP,
                CAB.CODTIPOPER,
                NVL(TOP.DESCROPER, '-') AS DESCROPER,
                CAB.CODVEND,
                NVL(VEN.APELIDO, TO_CHAR(CAB.CODVEND)) AS NOMEVEND,
                NVL(CAB.PENDENTE, 'N') AS PENDENTE,
                NVL(CAB.AD_PIX_LINK, '-') AS LINK_PIX,
                TO_CHAR(CAB.AD_PIX_DHATUAL, 'DD/MM/YYYY HH24:MI') AS DH_PIX
            FROM
                TGFCAB CAB
                LEFT JOIN TGFPAR PAR ON CAB.CODPARC = PAR.CODPARC
                LEFT JOIN TGFTOP TOP ON (CAB.CODTIPOPER = TOP.CODTIPOPER AND CAB.DHTIPOPER = TOP.DHALTER)
                LEFT JOIN TGFVEN VEN ON CAB.CODVEND = VEN.CODVEND
            WHERE
                {where_clause}
        ) WHERE RNUM BETWEEN {offset_inicio} AND {offset_fim}
        """

        dados = self._executar_sql(sql)
        pedidos = []
        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                vlr_nota = float(r[7]) if r[7] is not None else 0.0
                pedidos.append({
                    "nunota": r[1],
                    "numnota": r[2],
                    "statusnota": str(r[3]).strip(),
                    "libconf": str(r[4]).strip(),
                    "codparc": r[5],
                    "nomeparc": str(r[6]).strip() if r[6] else "CONSUMIDOR",
                    "vlrnota": round(vlr_nota, 2),
                    "dtneg": str(r[8]).strip() if r[8] else "-",
                    "codemp": r[9],
                    "codtipoper": r[10],
                    "descroper": str(r[11]).strip() if r[11] else "-",
                    "codvend": r[12],
                    "nomevend": str(r[13]).strip() if r[13] else "-",
                    "pendente": str(r[14]).strip(),
                    "link_pix": str(r[15]).strip() if r[15] else "-",
                    "dh_pix": str(r[16]).strip() if r[16] else "-"
                })
        return pedidos, total_registros, total_paginas, pagina

    def listar_itens_pedido_venda(self, nunota):
        """
        Consulta os itens de um pedido específico em TGFITE + TGFPRO.
        """
        if not self.jsessionid:
            self.autenticar()

        try:
            nunota = int(nunota)
        except (ValueError, TypeError):
            return []

        sql = f"""
        SELECT
            ITE.NUNOTA,
            ITE.SEQUENCIA,
            ITE.CODPROD,
            PRO.DESCRPROD,
            NVL(PRO.REFERENCIA, '-') AS REFERENCIA,
            NVL(PRO.REFFORN, '-') AS REFFORN,
            NVL(ITE.CODVOL, 'UN') AS CODVOL,
            NVL(ITE.QTDNEG, 0) AS QTDNEG,
            NVL(ITE.VLRUNIT, 0) AS VLRUNIT,
            NVL(ITE.VLRDESC, 0) AS VLRDESC,
            NVL(ITE.VLRTOT, 0) AS VLRTOT,
            NVL(ITE.PERCDESC, 0) AS PERCDESC,
            NVL(SNK_PRECO(0, ITE.CODPROD), ITE.VLRUNIT) AS PRECO_TABELA
        FROM
            TGFITE ITE
            INNER JOIN TGFPRO PRO ON ITE.CODPROD = PRO.CODPROD
        WHERE
            ITE.NUNOTA = {nunota}
        ORDER BY
            ITE.SEQUENCIA ASC
        """

        dados = self._executar_sql(sql)
        itens = []
        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                qtd = float(r[7]) if r[7] is not None else 0.0
                vlrunit = float(r[8]) if r[8] is not None else 0.0
                vlrdesc = float(r[9]) if r[9] is not None else 0.0
                vlrtot = float(r[10]) if r[10] is not None else 0.0
                percdesc = float(r[11]) if r[11] is not None else 0.0
                preco_tabela = float(r[12]) if len(r) > 12 and r[12] is not None else vlrunit

                # Reconstitui os dados de tabela e desconto promocional para exibição consistente na grade
                promo_val = self.obter_promocao_produto(codprod=r[2], codemp=1, codparc=0)
                if promo_val > 0 and preco_tabela > promo_val and vlrdesc <= 0.01 and abs(vlrunit - promo_val) <= 0.05:
                    vlr_unit_exibicao = preco_tabela
                    vlr_desc_exibicao = round((preco_tabela - vlrunit) * qtd, 2)
                    perc_desc_exibicao = round(((preco_tabela - vlrunit) / preco_tabela) * 100, 2)
                else:
                    vlr_unit_exibicao = vlrunit
                    vlr_desc_exibicao = vlrdesc
                    perc_desc_exibicao = percdesc

                itens.append({
                    "nunota": r[0],
                    "sequencia": r[1],
                    "codprod": r[2],
                    "descrprod": str(r[3]).strip() if r[3] else "",
                    "referencia": str(r[4]).strip() if r[4] else "-",
                    "refforn": str(r[5]).strip() if r[5] else "-",
                    "codvol": str(r[6]).strip() if r[6] else "UN",
                    "qtdneg": qtd,
                    "vlrunit": round(vlr_unit_exibicao, 2),
                    "vlrdesc": round(vlr_desc_exibicao, 2),
                    "vlrtot": round(vlrtot, 2),
                    "percdesc": round(perc_desc_exibicao, 2),
                    "promocao": round(promo_val, 2) if promo_val > 0 else 0.0,
                    "preco": round(preco_tabela, 2)
                })
        return itens

    def obter_imagem_produto(self, codprod):
        """
        Recupera os bytes da imagem do produto (TGFPRO.IMAGEM) diretamente do Sankhya.
        Retorna: (bytes_da_imagem, mimetype) ou (None, None)
        """
        try:
            codprod = int(codprod)
            sql_len = f"SELECT DBMS_LOB.GETLENGTH(IMAGEM) FROM TGFPRO WHERE CODPROD = {codprod}"
            res_len = self._executar_sql(sql_len)
            total_bytes = 0
            if res_len and res_len.get("status") == "1":
                rows = res_len.get("responseBody", {}).get("rows", [])
                if rows and rows[0] and rows[0][0]:
                    total_bytes = int(rows[0][0])

            if total_bytes <= 0:
                return None, None

            chunk_size = 2000
            hex_full = ""
            offset = 1
            while offset <= total_bytes:
                sql_chunk = f"SELECT RAWTOHEX(DBMS_LOB.SUBSTR(IMAGEM, {chunk_size}, {offset})) FROM TGFPRO WHERE CODPROD = {codprod}"
                res_chunk = self._executar_sql(sql_chunk)
                if res_chunk and res_chunk.get("status") == "1":
                    rows_c = res_chunk.get("responseBody", {}).get("rows", [])
                    if rows_c and rows_c[0] and rows_c[0][0]:
                        hex_full += rows_c[0][0]
                    else:
                        break
                else:
                    break
                offset += chunk_size

            if not hex_full:
                return None, None

            img_bytes = bytes.fromhex(hex_full)
            
            # Detecta o formato da imagem pelos magic bytes
            if img_bytes.startswith(b'\x89PNG'):
                mimetype = "image/png"
            elif img_bytes.startswith(b'\xff\xd8'):
                mimetype = "image/jpeg"
            elif img_bytes.startswith(b'GIF'):
                mimetype = "image/gif"
            elif img_bytes.startswith(b'BM'):
                mimetype = "image/bmp"
            elif img_bytes.startswith(b'RIFF') and b'WEBP' in img_bytes[:16]:
                mimetype = "image/webp"
            else:
                mimetype = "image/png"

            return img_bytes, mimetype
        except Exception as e:
            print(f"[Sankhya] Erro ao recuperar imagem do produto {codprod}: {e}")
            return None, None

    def cadastrar_produto(self, nome, preco, cod_grupo=1000):
        """Cadastra um novo produto na entidade Produto (TGFPRO)"""
        if not self.jsessionid:
            if not self.autenticar():
                return False

        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={self.jsessionid}"

        payload = {
            "serviceName": "CRUDServiceProvider.saveRecord",
            "requestBody": {
                "dataSet": {
                    "rootEntity": "Produto",
                    "includePresentationFields": "N",
                    "dataRow": {
                        "localFields": {
                            "DESCRPROD": {"$": nome},
                            "VLRVENDA": {"$": str(preco)},
                            "CODGRUPOPROD": {"$": str(cod_grupo)},
                            "USOPROD": {"$": "R"}  # R = Revenda
                        }
                    },
                    "entity": {
                        "fieldset": {
                            "list": "DESCRPROD,VLRVENDA,CODGRUPOPROD,USOPROD"
                        }
                    }
                }
            }
        }

        try:
            response = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            if response.status_code == 200:
                dados = response.json()
                sucesso = dados.get("status") == "1"
                if not sucesso:
                    print(f"[Sankhya] Erro ao salvar produto: {dados.get('statusMessage')}")
                return sucesso
            return False
        except Exception as e:
            print(f"[Sankhya] Erro ao cadastrar produto: {e}")
            return False

    def autenticar_usuario_sankhya(self, usuario, senha):
        """
        Autentica um usuário diretamente no Sankhya OM e verifica se possui AD_ADMCENTRALPED = 'S'.
        Retorna: (sucesso: bool, dados_usuario: dict, mensagem_erro: str)
        """
        usuario = (usuario or "").strip()
        senha = (senha or "").strip()

        if not usuario or not senha:
            return False, None, "Por favor, preencha o usuário e a senha."

        url = f"{self.base_url}/mge/service.sbr?serviceName=MobileLoginSP.login&outputType=json"
        payload = {
            "serviceName": "MobileLoginSP.login",
            "requestBody": {
                "NOMUSU": {"$": usuario},
                "INTERNO": {"$": senha},
                "KEEPCONNECTED": {"$": "S"}
            }
        }

        try:
            # Usa uma sessão HTTP isolada para autenticar o usuário, evitando que cookies/credenciais do usuário sobrescrevam a sessão de integração (integra.api)
            user_session = requests.Session()
            user_session.verify = False
            response = user_session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30, verify=False)
            if response.status_code == 200:
                dados = response.json()
                if dados.get("status") == "1":
                    jsessionid = dados.get("responseBody", {}).get("jsessionid", {}).get("$")
                    
                    # Consulta dados básicos na TSIUSU, TGFVEN e TSIGRU usando a sessão de integração do backend
                    codusu = None
                    codemp = 1
                    codvend = 0
                    codgrupo = 0
                    nome_grupo = "-"
                    nome_vend = "-"
                    usuario_sanitizado = re.sub(r"[^a-zA-Z0-9_.\-@]", "", str(usuario).strip()).replace("'", "''")
                    sql_usuario = f"""
                        SELECT 
                            U.CODUSU, 
                            U.NOMEUSU, 
                            NVL(U.CODEMP, 1) AS CODEMP, 
                            NVL(U.CODVEND, 0) AS CODVEND, 
                            NVL(U.CODGRUPO, 0) AS CODGRUPO, 
                            NVL(V.APELIDO, '-') AS NOMEVEND,
                            NVL(G.NOMEGRUPO, '-') AS NOMEGRUPO,
                            CASE 
                                WHEN U.DTLIMACESSO IS NOT NULL AND TRUNC(U.DTLIMACESSO) <= TRUNC(SYSDATE) THEN 'S' 
                                ELSE 'N' 
                            END AS EXPIRADO,
                            TO_CHAR(U.DTLIMACESSO, 'DD/MM/YYYY') AS DTLIMACESSO_STR
                        FROM 
                            TSIUSU U 
                            LEFT JOIN TGFVEN V ON U.CODVEND = V.CODVEND 
                            LEFT JOIN TSIGRU G ON U.CODGRUPO = G.CODGRUPO
                        WHERE 
                            UPPER(TRIM(U.NOMEUSU)) = UPPER('{usuario_sanitizado}')
                    """
                    res = self._executar_sql(sql_usuario)

                    if res and res.get("status") == "1" and res["responseBody"].get("rows"):
                        row = res["responseBody"]["rows"][0]
                        codusu = row[0]
                        nome_completo = row[1] or usuario.upper()
                        codemp = int(row[2]) if row[2] else 1
                        codvend = int(row[3]) if row[3] else 0
                        codgrupo = int(row[4]) if row[4] else 0
                        nome_vend = str(row[5]).strip() if row[5] else "-"
                        nome_grupo = str(row[6]).strip() if len(row) > 6 and row[6] else "-"
                        expirado = (str(row[7]).strip().upper() == "S") if len(row) > 7 and row[7] is not None else False
                        dt_limite = str(row[8]).strip() if len(row) > 8 and row[8] is not None else ""

                        # Validação de data limite de acesso (expiração se DTLIMACESSO <= hoje)
                        if expirado:
                            msg_exp = f"Usuário com acesso expirado no Sankhya (Data limite: {dt_limite})." if dt_limite else "Usuário com acesso expirado pela data limite."
                            return False, None, msg_exp
                    else:
                        # Fallback simples caso os joins falhem
                        codusu = 0
                        nome_completo = usuario.upper()

                    user_info = {
                        "codusu": codusu,
                        "nome": nome_completo,
                        "usuario": usuario.upper(),
                        "codemp": codemp,
                        "codvend": codvend,
                        "codgrupo": codgrupo,
                        "nomegrupo": nome_grupo,
                        "nomevend": nome_vend,
                        "jsessionid": jsessionid
                    }
                    return True, user_info, None

                else:
                    msg = dados.get("statusMessage", "Usuário ou senha incorretos no Sankhya.")
                    return False, None, msg
            else:
                return False, None, f"Erro de conexão com o Sankhya (HTTP {response.status_code})"
        except Exception as e:
            err_str = str(e)
            if "timed out" in err_str.lower() or "timeout" in err_str.lower():
                return False, None, "O servidor Sankhya demorou muito para responder (timeout). Tente novamente em alguns instantes."
            return False, None, f"Erro ao conectar ao servidor Sankhya: {err_str}"

    def listar_grupos_sankhya(self):
        """Lista todos os grupos de usuários cadastrados no Sankhya OM (TSIGRU)."""
        sql = """
            SELECT 
                G.CODGRUPO, 
                G.NOMEGRUPO,
                COUNT(U.CODUSU) AS TOTAL_USUARIOS
            FROM 
                TSIGRU G
                LEFT JOIN TSIUSU U ON G.CODGRUPO = U.CODGRUPO
            GROUP BY 
                G.CODGRUPO, 
                G.NOMEGRUPO
            ORDER BY 
                G.NOMEGRUPO ASC
        """
        res = self._executar_sql(sql)
        grupos = []
        if res and res.get("status") == "1":
            for r in res.get("responseBody", {}).get("rows", []):
                grupos.append({
                    "codgrupo": int(r[0]),
                    "nomegrupo": str(r[1]).strip() if r[1] else f"Grupo {r[0]}",
                    "total_usuarios": int(r[2]) if r[2] else 0
                })
        return grupos

    def listar_usuarios_sankhya(self):
        """Lista todos os usuários cadastrados no Sankhya OM (TSIUSU)."""
        sql = """
            SELECT 
                U.CODUSU, 
                U.NOMEUSU, 
                NVL(U.CODGRUPO, 0) AS CODGRUPO, 
                NVL(G.NOMEGRUPO, '-') AS NOMEGRUPO, 
                NVL(U.CODVEND, 0) AS CODVEND, 
                NVL(V.APELIDO, '-') AS NOMEVEND
            FROM 
                TSIUSU U
                LEFT JOIN TSIGRU G ON U.CODGRUPO = G.CODGRUPO
                LEFT JOIN TGFVEN V ON U.CODVEND = V.CODVEND
            ORDER BY 
                U.NOMEUSU ASC
        """
        res = self._executar_sql(sql)
        usuarios = []
        if res and res.get("status") == "1":
            for r in res.get("responseBody", {}).get("rows", []):
                usuarios.append({
                    "codusu": int(r[0]),
                    "nomeusu": str(r[1]).strip(),
                    "codgrupo": int(r[2]) if r[2] else 0,
                    "nomegrupo": str(r[3]).strip() if r[3] else "-",
                    "codvend": int(r[4]) if r[4] else 0,
                    "nomevend": str(r[5]).strip() if r[5] else "-"
                })
        return usuarios

    def listar_usuarios_acesso(self):
        """
        Retorna a lista de usuários que possuem permissão de acesso à Central de Pedidos:
        TSIUSU.AD_ADMCENTRALPED = 'S' OU TSIGRU.AD_ACESSACENTRALPED = 'S'
        """
        sql = """
            SELECT 
                U.CODUSU, 
                U.NOMEUSU, 
                NVL(U.AD_ADMCENTRALPED, 'N') AS AD_ADM, 
                NVL(U.CODGRUPO, 0) AS CODGRUPO, 
                NVL(G.AD_ACESSACENTRALPED, 'N') AS GRUPO_ACESSO,
                NVL(V.APELIDO, '-') AS NOMEVEND,
                TO_CHAR(U.DTLIMACESSO, 'DD/MM/YYYY') AS DTLIMACESSO_STR
            FROM 
                TSIUSU U 
                LEFT JOIN TSIGRU G ON U.CODGRUPO = G.CODGRUPO
                LEFT JOIN TGFVEN V ON U.CODVEND = V.CODVEND
            WHERE 
                (NVL(U.AD_ADMCENTRALPED, 'N') = 'S' OR NVL(G.AD_ACESSACENTRALPED, 'N') = 'S')
                AND (U.DTLIMACESSO IS NULL OR TRUNC(U.DTLIMACESSO) > TRUNC(SYSDATE))
            ORDER BY 
                U.CODUSU ASC
        """
        res = self._executar_sql(sql)
        usuarios = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                codusu = row[0]
                nomeusu = str(row[1]).strip() if row[1] else "-"
                ad_adm = str(row[2]).strip().upper() if row[2] is not None else "N"
                codgrupo = int(row[3]) if row[3] else 0
                grupo_acesso = str(row[4]).strip().upper() if row[4] is not None else "N"
                nome_vend = str(row[5]).strip() if row[5] else "-"
                dt_limite = str(row[6]).strip() if len(row) > 6 and row[6] is not None else "-"

                is_adm = (ad_adm == "S")
                has_grupo = (grupo_acesso == "S")

                if is_adm and has_grupo:
                    perfil = "ADMIN/GRUPO"
                    badge_style = "background: #7c3aed; color: #ffffff;"
                elif is_adm:
                    perfil = "ADMIN"
                    badge_style = "background: #00A859; color: #ffffff;"
                elif has_grupo:
                    perfil = "Grupo"
                    badge_style = "background: #0284c7; color: #ffffff;"
                else:
                    perfil = "-"
                    badge_style = "background: #94a3b8; color: #ffffff;"

                usuarios.append({
                    "codusu": codusu,
                    "nomeusu": nomeusu,
                    "ad_adm": ad_adm,
                    "is_adm": is_adm,
                    "codgrupo": codgrupo,
                    "grupo_acesso": grupo_acesso,
                    "has_grupo": has_grupo,
                    "nomevend": nome_vend,
                    "dt_limite": dt_limite,
                    "perfil": perfil,
                    "badge_style": badge_style
                })
        return usuarios

    def obter_empresa(self, codemp):
        """Busca os dados da empresa na TSIEMP pelo CODEMP"""
        codemp = int(codemp) if codemp else 1
        sql = f"""
            SELECT 
                E.CODEMP, 
                NVL(E.NOMEFANTASIA, NVL(E.RAZAOSOCIAL, 'EMPRESA ' || E.CODEMP)) AS NOMEEMP,
                NVL(E.RAZAOSOCIAL, '-') AS RAZAOSOCIAL,
                NVL(E.CGC, '-') AS CNPJ
            FROM TSIEMP E 
            WHERE E.CODEMP = {codemp}
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            row = res["responseBody"]["rows"][0]
            return {
                "codemp": row[0],
                "nomeemp": str(row[1]).strip() if row[1] else f"EMPRESA {codemp}",
                "razaosocial": str(row[2]).strip() if row[2] else "-",
                "cnpj": str(row[3]).strip() if row[3] else "-"
            }
        return {
            "codemp": codemp,
            "nomeemp": "AUTOGIRO JP" if codemp == 1 else f"EMPRESA {codemp}",
            "razaosocial": "-",
            "cnpj": "-"
        }

    def buscar_parceiros(self, busca=None, limite=50):
        """
        Busca parceiros clientes na tabela TGFPAR (CLIENTE = 'S')
        Permite filtrar por CODPARC, NOMEPARC, RAZAOSOCIAL ou CGC_CPF (CNPJ/CPF)
        """
        where_clauses = ["NVL(P.CLIENTE, 'N') = 'S'", "NVL(P.ATIVO, 'S') = 'S'"]

        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            busca_numerica = "".join(filter(str.isdigit, busca_clean))

            filtros = [
                f"UPPER(P.NOMEPARC) LIKE UPPER('%{busca_clean}%')",
                f"UPPER(P.RAZAOSOCIAL) LIKE UPPER('%{busca_clean}%')",
                f"P.CGC_CPF LIKE '%{busca_clean}%'",
                f"TO_CHAR(P.CODPARC) LIKE '%{busca_clean}%'"
            ]

            if busca_numerica and busca_numerica != busca_clean:
                filtros.append(f"REPLACE(REPLACE(REPLACE(P.CGC_CPF, '.', ''), '-', ''), '/', '') LIKE '%{busca_numerica}%'")

            where_clauses.append(f"({' OR '.join(filtros)})")

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT * FROM (
                SELECT 
                    P.CODPARC, 
                    NVL(P.NOMEPARC, '-') AS NOMEPARC, 
                    NVL(P.RAZAOSOCIAL, '-') AS RAZAOSOCIAL, 
                    NVL(P.CGC_CPF, '-') AS CGC_CPF,
                    NVL(P.TELEFONE, '-') AS TELEFONE,
                    NVL(P.EMAIL, '-') AS EMAIL,
                    NVL(P.CLIENTE, 'N') AS CLIENTE,
                    NVL(P.ATIVO, 'S') AS ATIVO
                FROM TGFPAR P
                WHERE {where_sql}
                ORDER BY P.NOMEPARC ASC
            ) WHERE ROWNUM <= {limite}
        """
        res = self._executar_sql(sql)
        parceiros = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                parceiros.append({
                    "codparc": row[0],
                    "nomeparc": str(row[1]).strip() if row[1] else "-",
                    "razaosocial": str(row[2]).strip() if row[2] else "-",
                    "cnpj_cpf": str(row[3]).strip() if row[3] else "-",
                    "telefone": str(row[4]).strip() if row[4] else "-",
                    "email": str(row[5]).strip() if row[5] else "-",
                    "cliente": str(row[6]).strip(),
                    "ativo": str(row[7]).strip()
                })
        return parceiros

    def obter_parceiro(self, codparc):
        """Busca um parceiro específico pelo CODPARC na TGFPAR (onde CLIENTE = 'S')"""
        if not codparc:
            return None
        sql = f"""
            SELECT 
                P.CODPARC, 
                NVL(P.NOMEPARC, '-') AS NOMEPARC, 
                NVL(P.RAZAOSOCIAL, '-') AS RAZAOSOCIAL, 
                NVL(P.CGC_CPF, '-') AS CGC_CPF,
                NVL(P.TELEFONE, '-') AS TELEFONE,
                NVL(P.EMAIL, '-') AS EMAIL,
                NVL(P.CLIENTE, 'N') AS CLIENTE,
                NVL(P.ATIVO, 'S') AS ATIVO
            FROM TGFPAR P
            WHERE P.CODPARC = {int(codparc)}
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            row = res["responseBody"]["rows"][0]
            return {
                "codparc": row[0],
                "nomeparc": str(row[1]).strip() if row[1] else "-",
                "razaosocial": str(row[2]).strip() if row[2] else "-",
                "cnpj_cpf": str(row[3]).strip() if row[3] else "-",
                "telefone": str(row[4]).strip() if row[4] else "-",
                "email": str(row[5]).strip() if row[5] else "-",
                "cliente": str(row[6]).strip(),
                "ativo": str(row[7]).strip()
            }
        return None

    def listar_tipos_operacao(self):
        """
        Retorna os tipos de operação (TGFTOP) permitidos para a Central de Pedidos.
        """
        sql = """
            SELECT 
                TPO.CODTIPOPER,
                NVL(TPO.DESCROPER, '-') AS DESCROPER
            FROM
                TGFTOP TPO
            WHERE 1 = 1
                AND TPO.DHALTER = (SELECT MAX(DHALTER) FROM TGFTOP WHERE CODTIPOPER = TPO.CODTIPOPER)    
                AND TPO.TIPMOV = 'P'
                AND TPO.ATIVO = 'S'
                AND TPO.CODTIPOPER <> 0
                AND TPO.ATUALFIN = 1
                AND TPO.TIPATUALFIN = 'P'
                AND TPO.CODTIPOPER IN (10002, 10052)
            ORDER BY
                TPO.CODTIPOPER ASC
        """
        res = self._executar_sql(sql)
        tipos_oper = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                tipos_oper.append({
                    "codtipoper": row[0],
                    "descroper": str(row[1]).strip() if row[1] else "-"
                })
        return tipos_oper

    def obter_tipo_operacao(self, codtipoper):
        """Busca a descrição de um tipo de operação específico na TGFTOP (apenas permitidos para Central de Pedidos)"""
        if not codtipoper:
            return None
        tipos_permitidos = self.listar_tipos_operacao()
        for t in tipos_permitidos:
            if str(t.get("codtipoper")) == str(codtipoper):
                return t
        return None

    def obter_vendedor(self, codvend):
        """Busca o apelido/nome do vendedor na TGFVEN pelo CODVEND"""
        codvend = int(codvend) if codvend else 0
        if not codvend:
            return {"codvend": 0, "nomevend": "-"}
        sql = f"""
            SELECT 
                CODVEND, 
                NVL(APELIDO, '-') AS NOMEVEND 
            FROM TGFVEN 
            WHERE CODVEND = {codvend}
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            row = res["responseBody"]["rows"][0]
            return {"codvend": row[0], "nomevend": str(row[1]).strip() if row[1] else "-"}
        return {"codvend": codvend, "nomevend": "-"}

    def buscar_vendedores(self, busca=None, limite=50):
        """Busca vendedores ativos na TGFVEN (ATIVO = 'S')"""
        where_clauses = ["NVL(V.ATIVO, 'S') = 'S'"]
        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            filtros = [
                f"UPPER(V.APELIDO) LIKE UPPER('%{busca_clean}%')",
                f"TO_CHAR(V.CODVEND) LIKE '%{busca_clean}%'"
            ]
            where_clauses.append(f"({' OR '.join(filtros)})")
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT * FROM (
                SELECT 
                    V.CODVEND, 
                    NVL(V.APELIDO, '-') AS APELIDO,
                    NVL(V.ATIVO, 'S') AS ATIVO
                FROM TGFVEN V
                WHERE {where_sql}
                ORDER BY V.APELIDO ASC
            ) WHERE ROWNUM <= {limite}
        """
        res = self._executar_sql(sql)
        vendedores = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                vendedores.append({
                    "codvend": row[0],
                    "nomevend": str(row[1]).strip() if row[1] else "-",
                    "ativo": row[2]
                })
        return vendedores

    def obter_produto(self, codprod):
        """Busca os dados de um produto na TGFPRO pelo CODPROD com referência, marca, unidade e preço padrão"""
        codprod = int(codprod) if codprod else 0
        if not codprod:
            return None
        sql = f"""
            SELECT 
                P.CODPROD, 
                NVL(P.DESCRPROD, '-') AS DESCRPROD,
                NVL(P.REFFORN, '-') AS REFFORN,
                NVL(P.MARCA, '-') AS MARCA,
                NVL(P.CODVOL, 'UN') AS CODVOL,
                NVL(SNK_PRECO(0, P.CODPROD), 0) AS PRECO
            FROM TGFPRO P 
            WHERE P.CODPROD = {codprod}
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            row = res["responseBody"]["rows"][0]
            preco_val = float(row[5]) if row[5] is not None else 0.0
            return {
                "codprod": row[0],
                "descrprod": str(row[1]).strip() if row[1] else "-",
                "refforn": str(row[2]).strip() if row[2] else "-",
                "marca": str(row[3]).strip() if row[3] else "-",
                "codvol": str(row[4]).strip() if row[4] else "UN",
                "preco": preco_val
            }
        return None

    def buscar_empresas(self, busca=None, limite=50):
        """Busca empresas na TSIEMP"""
        where_clauses = ["1=1"]
        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            filtros = [
                f"UPPER(E.NOMEFANTASIA) LIKE UPPER('%{busca_clean}%')",
                f"UPPER(E.RAZAOSOCIAL) LIKE UPPER('%{busca_clean}%')",
                f"TO_CHAR(E.CODEMP) LIKE '%{busca_clean}%'"
            ]
            where_clauses.append(f"({' OR '.join(filtros)})")
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT * FROM (
                SELECT 
                    E.CODEMP, 
                    NVL(E.NOMEFANTASIA, NVL(E.RAZAOSOCIAL, 'EMPRESA ' || E.CODEMP)) AS NOMEEMP,
                    NVL(E.RAZAOSOCIAL, '-') AS RAZAOSOCIAL,
                    NVL(E.CGC, '-') AS CNPJ
                FROM TSIEMP E 
                WHERE {where_sql}
                ORDER BY E.CODEMP ASC
            ) WHERE ROWNUM <= {limite}
        """
        res = self._executar_sql(sql)
        empresas = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                empresas.append({
                    "codemp": row[0],
                    "nomeemp": str(row[1]).strip() if row[1] else f"EMPRESA {row[0]}",
                    "razaosocial": str(row[2]).strip() if row[2] else "-",
                    "cnpj": str(row[3]).strip() if row[3] else "-"
                })
        return empresas

    def buscar_regioes(self, busca=None, limite=50):
        """Busca regiões na TGFREG"""
        where_clauses = ["1=1"]
        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            filtros = [
                f"UPPER(R.DESCRREGRA) LIKE UPPER('%{busca_clean}%')",
                f"TO_CHAR(R.CODREGRA) LIKE '%{busca_clean}%'"
            ]
            where_clauses.append(f"({' OR '.join(filtros)})")
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT * FROM (
                SELECT 
                    R.CODREGRA AS CODREG, 
                    NVL(R.DESCRREGRA, 'REGIÃO ' || R.CODREGRA) AS NOMEREG
                FROM TGFREG R 
                WHERE {where_sql}
                ORDER BY R.CODREGRA ASC
            ) WHERE ROWNUM <= {limite}
        """
        res = self._executar_sql(sql)
        regioes = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                regioes.append({
                    "codreg": row[0],
                    "nomereg": str(row[1]).strip() if row[1] else f"REGIÃO {row[0]}"
                })
        return regioes

    def buscar_cidades(self, busca=None, limite=50):
        """Busca cidades na TSICID"""
        where_clauses = ["1=1"]
        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            filtros = [
                f"UPPER(C.NOMECID) LIKE UPPER('%{busca_clean}%')",
                f"TO_CHAR(C.CODCID) LIKE '%{busca_clean}%'"
            ]
            where_clauses.append(f"({' OR '.join(filtros)})")
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT * FROM (
                SELECT 
                    C.CODCID, 
                    NVL(C.NOMECID, 'CIDADE ' || C.CODCID) AS NOMECID,
                    NVL(TO_CHAR(C.UF), '-') AS UF
                FROM TSICID C 
                WHERE {where_sql}
                ORDER BY C.NOMECID ASC
            ) WHERE ROWNUM <= {limite}
        """
        res = self._executar_sql(sql)
        cidades = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                cidades.append({
                    "codcid": row[0],
                    "nomecid": str(row[1]).strip() if row[1] else f"CIDADE {row[0]}",
                    "uf": str(row[2]).strip() if row[2] else "-"
                })
        return cidades

    def buscar_bairros(self, busca=None, limite=50):
        """Busca bairros na TSIBAI"""
        where_clauses = ["1=1"]
        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            filtros = [
                f"UPPER(B.NOMEBAI) LIKE UPPER('%{busca_clean}%')",
                f"TO_CHAR(B.CODBAI) LIKE '%{busca_clean}%'"
            ]
            where_clauses.append(f"({' OR '.join(filtros)})")
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT * FROM (
                SELECT 
                    B.CODBAI, 
                    NVL(B.NOMEBAI, 'BAIRRO ' || B.CODBAI) AS NOMEBAI
                FROM TSIBAI B 
                WHERE {where_sql}
                ORDER BY B.NOMEBAI ASC
            ) WHERE ROWNUM <= {limite}
        """
        res = self._executar_sql(sql)
        bairros = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                bairros.append({
                    "codbai": row[0],
                    "nomebai": str(row[1]).strip() if row[1] else f"BAIRRO {row[0]}"
                })
        return bairros

    def buscar_locais(self, busca=None, limite=50):
        """Busca locais na TGFLOC"""
        where_clauses = ["1=1"]
        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            filtros = [
                f"UPPER(L.DESCRLOCAL) LIKE UPPER('%{busca_clean}%')",
                f"TO_CHAR(L.CODLOCAL) LIKE '%{busca_clean}%'"
            ]
            where_clauses.append(f"({' OR '.join(filtros)})")
        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT * FROM (
                SELECT 
                    L.CODLOCAL, 
                    NVL(L.DESCRLOCAL, 'LOCAL ' || L.CODLOCAL) AS DESCRLOCAL
                FROM TGFLOC L 
                WHERE {where_sql}
                ORDER BY L.CODLOCAL ASC
            ) WHERE ROWNUM <= {limite}
        """
        res = self._executar_sql(sql)
        locais = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                locais.append({
                    "codlocal": row[0],
                    "descrlocal": str(row[1]).strip() if row[1] else f"LOCAL {row[0]}"
                })
        return locais

    def obter_tabela_preco_parceiro(self, codparc):
        """
        Busca a tabela de preço vinculada ao parceiro (TGFPAR -> TGFTPP -> TGFNTA)
        """
        if not codparc:
            return {"codtab": 0, "nometab": "PADRÃO (AUTO GIRO)"}
        sql = f"""
            SELECT
                TPP.CODTAB,
                NVL(NTA.NOMETAB, 'TABELA ' || TPP.CODTAB) AS NOMETAB
            FROM
                TGFPAR PAR
                INNER JOIN TGFTPP TPP ON PAR.CODTIPPARC = TPP.CODTIPPARC
                INNER JOIN TGFNTA NTA ON TPP.CODTAB = NTA.CODTAB
            WHERE
                PAR.CODPARC = {int(codparc)}
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            row = res["responseBody"]["rows"][0]
            return {
                "codtab": row[0],
                "nometab": str(row[1]).strip() if row[1] else f"TABELA {row[0]}"
            }
        return {"codtab": 0, "nometab": "PADRÃO (AUTO GIRO)"}

    def listar_tipos_negociacao(self, codparc=1158, codtipoper=10002):
        """
        Retorna os tipos de negociação (TGFTPV) permitidos para o Parceiro e TOP selecionada.
        """
        codparc = int(codparc) if codparc else 1158
        codtipoper = int(codtipoper) if codtipoper else 10002

        sql = f"""
            SELECT DISTINCT
                TPV.CODTIPVENDA,
                NVL(TPV.DESCRTIPVENDA, 'NEGOCIAÇÃO ' || TPV.CODTIPVENDA) AS DESCRTIPVENDA
            FROM 
                TGFTPV TPV
            INNER JOIN 
                TGFPAR PAR ON PAR.CODPARC = {codparc}
            WHERE 
                TPV.DHALTER = (SELECT MAX(DHALTER) FROM TGFTPV WHERE CODTIPVENDA = TPV.CODTIPVENDA)
                AND INSTR(',' || PAR.GRUPOAUTOR || ',', ',' || TPV.GRUPOAUTOR || ',') > 0
                AND EXISTS(SELECT 1 FROM TGFREP WHERE CODTIPOPER = {codtipoper} AND TIPREST = 'T' AND RESTRICAO = 'S' AND CODCOLREST = TPV.CODTIPVENDA)
            ORDER BY
                TPV.CODTIPVENDA ASC
        """
        res = self._executar_sql(sql)
        tipos_neg = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                tipos_neg.append({
                    "codtipvenda": row[0],
                    "descrtipvenda": str(row[1]).strip() if row[1] else f"NEGOCIAÇÃO {row[0]}"
                })
        return tipos_neg

    def obter_tipo_negociacao(self, codtipvenda):
        """Busca um tipo de negociação específico na TGFTPV"""
        if not codtipvenda:
            return None
        sql = f"""
            SELECT 
                TPV.CODTIPVENDA,
                NVL(TPV.DESCRTIPVENDA, 'NEGOCIAÇÃO ' || TPV.CODTIPVENDA) AS DESCRTIPVENDA
            FROM TGFTPV TPV
            WHERE TPV.DHALTER = (SELECT MAX(DHALTER) FROM TGFTPV WHERE CODTIPVENDA = TPV.CODTIPVENDA)
              AND TPV.CODTIPVENDA = {int(codtipvenda)}
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            row = res["responseBody"]["rows"][0]
            return {
                "codtipvenda": row[0],
                "descrtipvenda": str(row[1]).strip() if row[1] else f"NEGOCIAÇÃO {row[0]}"
            }
        return None

    def obter_sugestao_negociacao_parceiro(self, codparc):
        """
        Busca a sugestão de tipo de negociação de saída na TGFCPL pelo CODPARC
        """
        if not codparc:
            return 0
        sql = f"SELECT NVL(SUGTIPNEGSAID, 0) AS SUGTIPNEGSAID FROM TGFCPL WHERE CODPARC = {int(codparc)}"
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            val = res["responseBody"]["rows"][0][0]
            return int(val) if val else 0
        return 0

    def obter_negociacao_padrao(self, codparc=1158, codtipoper=10002):
        """
        Retorna a negociação padrão para o parceiro e TOP:
        1. Verifica se há SUGTIPNEGSAID em TGFCPL
        2. Valida se a sugestão é permitida pela consulta de TGFTPV/TGFREP
        3. Se permitida, retorna ela; caso contrário, retorna a primeira permitida.
        """
        tipos_permitidos = self.listar_tipos_negociacao(codparc=codparc, codtipoper=codtipoper)
        if not tipos_permitidos:
            return None

        sugestao_cod = self.obter_sugestao_negociacao_parceiro(codparc)
        if sugestao_cod:
            for tpv in tipos_permitidos:
                if int(tpv["codtipvenda"]) == int(sugestao_cod):
                    return tpv

        return tipos_permitidos[0]

    def obter_limite_credito_parceiro(self, codparc):
        """
        Calcula e retorna o saldo de limite de crédito do parceiro via TGFFIN e TGFPAR.
        """
        if not codparc:
            return "(Saldo 0,00) 0,00/0,00"

        sql = f"""
            WITH LIMITE_UTILIZADO_CTE AS (
                SELECT
                    FIN.CODPARC,
                    COALESCE(SUM(FIN.VLRDESDOB), 0) AS VLR_UTILIZADO
                FROM
                    TGFFIN FIN
                    INNER JOIN TGFTIT TIT ON FIN.CODTIPTIT = TIT.CODTIPTIT
                WHERE
                    FIN.RECDESP = 1
                    AND FIN.DHBAIXA IS NULL
                    AND FIN.PROVISAO = 'N'
                    AND TIT.SUBTIPOVENDA NOT IN (1, 10, 11)
                GROUP BY
                    FIN.CODPARC
            )
            SELECT
                 '(Saldo ' || TO_CHAR((COALESCE(PAR.LIMCRED, 0) - COALESCE(CTE.VLR_UTILIZADO, 0)),'FM999G999G990D90') || ') ' || TO_CHAR(COALESCE(CTE.VLR_UTILIZADO, 0),'FM999G999G990D90') || '/' || 
                TO_CHAR(COALESCE(PAR.LIMCRED, 0),'FM999G999G990D90')  AS SALDO
            FROM
                TGFPAR PAR
                LEFT JOIN LIMITE_UTILIZADO_CTE CTE ON PAR.CODPARC = CTE.CODPARC
            WHERE
                PAR.ATIVO = 'S'
                AND PAR.CLIENTE = 'S'
                AND PAR.CODPARC = {int(codparc)}
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            val = res["responseBody"]["rows"][0][0]
            return str(val).strip() if val else "(Saldo 0,00) 0,00/0,00"
        return "(Saldo 0,00) 0,00/0,00"

    def obter_parametro_tsipar_texto(self, chave):
        """
        Consulta o campo TEXTO de um parâmetro na tabela TSIPAR do Sankhya.
        Mantém cache em memória para otimizar desempenho.
        """
        if not hasattr(self, "_cache_tsipar_texto") or self._cache_tsipar_texto is None:
            self._cache_tsipar_texto = {}

        if chave in self._cache_tsipar_texto:
            return self._cache_tsipar_texto[chave]

        sql = f"SELECT TEXTO FROM TSIPAR WHERE CHAVE = '{chave}'"
        res = self._executar_sql(sql)
        texto = None
        if res and res.get("status") == "1":
            rows = res.get("responseBody", {}).get("rows", [])
            if rows and len(rows) > 0 and rows[0][0] and str(rows[0][0]).strip():
                texto = str(rows[0][0]).strip()

        if texto:
            self._cache_tsipar_texto[chave] = texto
        return texto

    def obter_credito_cliente_parametro(self, codparc):
        """
        Consulta o valor de crédito do cliente utilizando a consulta SQL
        configurada no parâmetro CONVLRCREPORTPE da tabela TSIPAR.
        """
        if not codparc:
            return 0.0

        fallback_sql = """
        SELECT NVL(SUM(FIN.VLRDESDOB),0)
        FROM TGFFIN FIN
        LEFT JOIN TGFPAR PAR ON PAR.CODPARC = FIN.CODPARC
        INNER JOIN TGFTIT TIT ON FIN.CODTIPTIT = TIT.CODTIPTIT
        WHERE FIN.RECDESP = 1
          AND FIN.DHBAIXA IS NULL
          AND FIN.PROVISAO = 'N'
          AND PAR.CLIENTE = 'S'
          AND TIT.SUBTIPOVENDA NOT IN(1,10,11)
          AND PAR.CODPARC = :CODPARC
        """
        sql_template = self.obter_parametro_tsipar_texto("CONVLRCREPORTPE") or fallback_sql
        sql = re.sub(r':CODPARC\b', str(int(codparc)), sql_template, flags=re.IGNORECASE)
        
        res = self._executar_sql(sql)
        if res and res.get("status") == "1":
            rows = res.get("responseBody", {}).get("rows", [])
            if rows and len(rows) > 0 and rows[0][0] is not None:
                try:
                    return float(rows[0][0])
                except (ValueError, TypeError):
                    return 0.0
        return 0.0

    def obter_atraso_cliente_parametro(self, codparc):
        """
        Consulta o valor e se há atraso do cliente utilizando a consulta SQL
        configurada no parâmetro CONVLRATRPORPED da tabela TSIPAR.
        """
        if not codparc:
            return 0.0

        fallback_sql = """
        SELECT SUM(FIN.VLRDESDOB)
        FROM TGFFIN FIN
        WHERE FIN.DHBAIXA IS NULL
          AND FIN.RECDESP = 1
          AND FIN.DTVENC IS NOT NULL
          AND TRUNC(FIN.DTVENC) < TRUNC(SYSDATE)
          AND FIN.CODPARC = :CODPARC
        """
        sql_template = self.obter_parametro_tsipar_texto("CONVLRATRPORPED") or fallback_sql
        sql = re.sub(r':CODPARC\b', str(int(codparc)), sql_template, flags=re.IGNORECASE)
        
        res = self._executar_sql(sql)
        if res and res.get("status") == "1":
            rows = res.get("responseBody", {}).get("rows", [])
            if rows and len(rows) > 0 and rows[0][0] is not None:
                try:
                    return float(rows[0][0])
                except (ValueError, TypeError):
                    return 0.0
    def obter_promocao_produto(self, codprod, codemp=1, codparc=0):
        """
        Consulta o valor de promoção do produto a partir do parâmetro CONSPROMPORTPED da TSIPAR.
        Recebe :CODEMP, :CODPARC e :CODPROD.
        Retorna o valor da promoção (float) ou 0.0.
        """
        if not codprod:
            return 0.0

        codemp_val = str(int(codemp)) if codemp else "1"
        codparc_val = str(int(codparc)) if codparc else "0"
        codprod_val = str(int(codprod))

        sql_template = self.obter_parametro_tsipar_texto("CONSPROMPORTPED")
        if not sql_template:
            return 0.0

        sql = re.sub(r':CODEMP\b', codemp_val, sql_template, flags=re.IGNORECASE)
        sql = re.sub(r':CODPARC\b', codparc_val, sql, flags=re.IGNORECASE)
        sql = re.sub(r':CODPROD\b', codprod_val, sql, flags=re.IGNORECASE)

        res = self._executar_sql(sql)
        if res and res.get("status") == "1":
            rows = res.get("responseBody", {}).get("rows", [])
            if rows and len(rows) > 0 and rows[0][0] is not None:
                try:
                    return float(rows[0][0])
                except (ValueError, TypeError):
                    return 0.0
        return 0.0

    def obter_status_financeiro_parceiro(self, codparc):
        """
        Retorna o status financeiro completo do parceiro:
        - tem_atraso: se há títulos vencidos a receber (usando o parâmetro CONVLRATRPORPED da TSIPAR)
        - vlr_atraso: valor total em atraso
        - tem_credito: se há crédito disponível para o cliente (usando o parâmetro CONVLRCREPORTPE da TSIPAR)
        - vlr_credito: valor do crédito disponível
        - bloqueio: status de bloqueio comercial (TGFPAR.BLOQUEAR)
        """
        if not codparc:
            return {
                "tem_atraso": False,
                "vlr_atraso": 0.0,
                "qtd_atraso": 0,
                "tem_credito": False,
                "vlr_credito": 0.0,
                "qtd_credito": 0,
                "bloqueio": "N"
            }

        vlr_atraso = self.obter_atraso_cliente_parametro(codparc)
        vlr_credito = self.obter_credito_cliente_parametro(codparc)

        bloqueio = "N"
        try:
            sql_bloq = f"SELECT NVL(BLOQUEAR, 'N') FROM TGFPAR WHERE CODPARC = {int(codparc)}"
            res_b = self._executar_sql(sql_bloq)
            if res_b and res_b.get("status") == "1":
                rows_b = res_b.get("responseBody", {}).get("rows", [])
                if rows_b and len(rows_b) > 0 and rows_b[0][0]:
                    bloqueio = str(rows_b[0][0]).strip()
        except Exception:
            pass

        return {
            "tem_atraso": bool(vlr_atraso and vlr_atraso > 0),
            "vlr_atraso": vlr_atraso,
            "qtd_atraso": 1 if (vlr_atraso and vlr_atraso > 0) else 0,
            "tem_credito": bool(vlr_credito and vlr_credito > 0),
            "vlr_credito": vlr_credito,
            "qtd_credito": 1 if (vlr_credito and vlr_credito > 0) else 0,
            "bloqueio": bloqueio
        }

    def obter_proximo_nunota(self):
        """Obtém o próximo NUNOTA da função SNK_GET_NUNOTA do Sankhya"""
        res = self._executar_sql("SELECT SNK_GET_NUNOTA FROM DUAL")
        if res and res.get("status") == "1":
            rows = res.get("responseBody", {}).get("rows", [])
            if rows and len(rows) > 0 and rows[0][0]:
                return int(rows[0][0])
        return None

    def verificar_pedido_faturado(self, nunota):
        """
        Verifica se o pedido está confirmado (STATUSNOTA = 'L') e já foi faturado gerando nota de venda
        (vínculo existente na tabela TGFVAR onde NUNOTA = :nunota ou NUNOTAORIG = :nunota).
        Retorna: (faturado: bool, statusnota: str)
        """
        if not self.jsessionid:
            self.autenticar()
        try:
            sql = f"""
            SELECT 
                NVL(CAB.STATUSNOTA, 'P') AS STATUSNOTA,
                CASE WHEN EXISTS(SELECT 1 FROM TGFVAR VAR WHERE VAR.NUNOTA = CAB.NUNOTA OR VAR.NUNOTAORIG = CAB.NUNOTA) THEN 'S' ELSE 'N' END AS FATURADO
            FROM TGFCAB CAB
            WHERE CAB.NUNOTA = {int(nunota)}
            """
            res = self._executar_sql(sql)
            if res and res.get("status") == "1" and res.get("responseBody", {}).get("rows"):
                row = res["responseBody"]["rows"][0]
                statusnota = str(row[0]).strip().upper() if row[0] else "P"
                faturado_var = str(row[1]).strip().upper() == "S" if len(row) > 1 and row[1] else False
                faturado = (statusnota == "L" and faturado_var)
                return faturado, statusnota
        except Exception as e:
            print(f"[Sankhya] Erro ao verificar faturamento do pedido #{nunota}: {e}")
        return False, "P"

    def salvar_cabecalho_pedido(self, codemp, codparc, codtipoper, codtipvenda, dtneg=None, codvend=None, observacao=None, cif_fob='C', vlrfrete=None, nunota=None, codusu=None, jsessionid=None):
        """
        Insere ou atualiza um cabeçalho de pedido na tabela TGFCAB via Sankhya API.
        Se nunota não for informado (ou None/0), obtém novo NUNOTA via SNK_GET_NUNOTA().
        Se nunota for informado, atualiza o pedido existente na TGFCAB.
        Grava o usuário logado (CODUSU / CODUSUINC) na TGFCAB.
        Retorna: (sucesso: bool, nunota: int | None, mensagem: str)
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            if not self.autenticar():
                return False, None, "Não foi possível autenticar na API do Sankhya."
            session_to_use = self.jsessionid

        is_update = False
        if nunota and int(nunota) > 0:
            nunota = int(nunota)
            is_update = True
            faturado, _ = self.verificar_pedido_faturado(nunota)
            if faturado:
                return False, nunota, "Este pedido já foi faturado e gerou uma nota de venda, portanto não pode ser alterado."
        else:
            nunota = self.obter_proximo_nunota()
            if not nunota:
                return False, None, "Não foi possível gerar o número do pedido."

        # Trata data de negociação (espera formato DD/MM/AAAA)
        dtneg_str = str(dtneg).strip() if dtneg else datetime.now().strftime("%d/%m/%Y")

        local_fields = {
            "CODEMP": {"$": str(codemp or 1)},
            "CODEMPNEGOC": {"$": str(codemp or 1)},
            "CODPARC": {"$": str(codparc)},
            "CODTIPOPER": {"$": str(codtipoper or 10002)},
            "CODTIPVENDA": {"$": str(codtipvenda or 1)},
            "DTNEG": {"$": dtneg_str},
            "CIF_FOB": {"$": str(cif_fob or 'C').strip()}
        }

        field_list = ["CODEMP", "CODEMPNEGOC", "CODPARC", "CODTIPOPER", "CODTIPVENDA", "DTNEG", "CIF_FOB"]

        if not is_update:
            local_fields["NUNOTA"] = {"$": str(nunota)}
            local_fields["NUMNOTA"] = {"$": "0"}
            local_fields["TIPMOV"] = {"$": "P"}
            local_fields["STATUSNOTA"] = {"$": "P"}
            field_list.extend(["NUNOTA", "NUMNOTA", "TIPMOV", "STATUSNOTA"])

        if codusu is not None:
            try:
                codusu_int = int(codusu)
                local_fields["CODUSU"] = {"$": str(codusu_int)}
                field_list.append("CODUSU")
                local_fields["CODUSUINC"] = {"$": str(codusu_int)}
                if "CODUSUINC" not in field_list:
                    field_list.append("CODUSUINC")
            except (ValueError, TypeError):
                pass

        if codvend:
            local_fields["CODVEND"] = {"$": str(codvend)}
            field_list.append("CODVEND")

        if observacao is not None:
            local_fields["OBSERVACAO"] = {"$": str(observacao).strip()}
            field_list.append("OBSERVACAO")

        if vlrfrete is not None:
            try:
                vlrfrete_num = float(str(vlrfrete).replace(".", "").replace(",", ".")) if isinstance(vlrfrete, str) else float(vlrfrete)
                local_fields["VLRFRETE"] = {"$": f"{vlrfrete_num:.2f}"}
                field_list.append("VLRFRETE")
            except (ValueError, TypeError):
                pass


        # Busca DHTIPOPER e DHTIPVENDA mais recentes para a TOP e TPV informadas
        try:
            sql_dh = f"""
            SELECT 
              (SELECT TO_CHAR(MAX(DHALTER), 'DD/MM/YYYY HH24:MI:SS') FROM TGFTOP WHERE CODTIPOPER = {codtipoper or 10002}),
              (SELECT TO_CHAR(MAX(DHALTER), 'DD/MM/YYYY HH24:MI:SS') FROM TGFTPV WHERE CODTIPVENDA = {codtipvenda or 1})
            FROM DUAL
            """
            res_dh = self._executar_sql(sql_dh)
            if res_dh and res_dh.get("status") == "1" and res_dh.get("responseBody", {}).get("rows"):
                rows_dh = res_dh["responseBody"]["rows"][0]
                if rows_dh[0]:
                    local_fields["DHTIPOPER"] = {"$": str(rows_dh[0])}
                    field_list.append("DHTIPOPER")
                if rows_dh[1]:
                    local_fields["DHTIPVENDA"] = {"$": str(rows_dh[1])}
                    field_list.append("DHTIPVENDA")
        except Exception as e_dh:
            print(f"[Sankhya] Aviso ao buscar DHALTER para TOP/TPV: {e_dh}")

        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
        payload = {
            "serviceName": "CRUDServiceProvider.saveRecord",
            "requestBody": {
                "dataSet": {
                    "rootEntity": "CabecalhoNota",
                    "includePresentationFields": "S",
                    "dataRow": {
                        "key": {"NUNOTA": {"$": str(nunota)}} if is_update else {},
                        "localFields": local_fields
                    },
                    "entity": {
                        "fieldset": {
                            "list": ",".join(field_list)
                        }
                    }
                }
            }
        }

        try:
            r = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200:
                dados = r.json()
                if dados.get("status") == "1":
                    acao = "atualizado" if is_update else "criado"
                    print(f"[Sankhya] Pedido #{nunota} {acao} com sucesso em TGFCAB!")
                    if codusu:
                        self._atualizar_usuario_solicitante_pedido(nunota, codusu, jsessionid=session_to_use)
                    return True, nunota, f"Pedido #{nunota} {acao} com sucesso!"
                elif dados.get("status") == "3" or "Sessão" in str(dados.get("statusMessage", "")) or "expirada" in str(dados.get("statusMessage", "")):
                    print("[Sankhya] Sessão expirada no saveRecord. Reautenticando...")
                    if self.autenticar():
                        url_retry = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={self.jsessionid}"
                        r_retry = self.session.post(url_retry, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
                        if r_retry.status_code == 200 and r_retry.json().get("status") == "1":
                            if codusu:
                                self._atualizar_usuario_solicitante_pedido(nunota, codusu, jsessionid=self.jsessionid)
                            return True, nunota, f"Pedido #{nunota} salvo com sucesso após retry!"
                        msg_erro = r_retry.json().get("statusMessage", "Erro ao gravar TGFCAB.")
                        return False, None, msg_erro
                else:
                    msg_erro = dados.get("statusMessage", "Erro desconhecido ao gravar TGFCAB.")
                    print(f"[Sankhya] Erro ao gravar TGFCAB: {msg_erro}")
                    return False, None, msg_erro
            else:
                return False, None, f"Falha HTTP {r.status_code} ao comunicar com o Sankhya."
        except Exception as e:
            print(f"[Sankhya] Exceção ao gravar TGFCAB: {e}")
            return False, None, str(e)

    def criar_cabecalho_pedido(self, codemp, codparc, codtipoper, codtipvenda, dtneg=None, codvend=None, observacao=None, cif_fob='C', nunota=None, codusu=None, jsessionid=None):
        """Alias para salvar_cabecalho_pedido"""
        return self.salvar_cabecalho_pedido(
            codemp=codemp, codparc=codparc, codtipoper=codtipoper, codtipvenda=codtipvenda,
            dtneg=dtneg, codvend=codvend, observacao=observacao, cif_fob=cif_fob, nunota=nunota, codusu=codusu, jsessionid=jsessionid
        )

    def atualizar_campo_cabecalho(self, nunota, campo, valor, codusu=None, jsessionid=None):
        """
        Atualiza um campo específico do cabeçalho da nota na TGFCAB com lista estrita de campos permitidos.
        """
        CAMPOS_PERMITIDOS = {
            "CODPARC", "CODTIPOPER", "CODTIPVENDA", "CODEMP", "CODVEND",
            "OBSERVACAO", "CIF_FOB", "DTNEG", "ORDEMCARGA", "TIPFRETE",
            "VLRFRETE", "CODPARCTRANSP", "OBSERVACAO_INTERNA", "VLRNOTA"
        }
        campo_upper = str(campo).strip().upper()
        if campo_upper not in CAMPOS_PERMITIDOS:
            return False, f"Campo '{campo}' não permitido para alteração direta."

        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            if not self.autenticar():
                return False, "Não foi possível autenticar na API do Sankhya."
            session_to_use = self.jsessionid

        try:
            nunota = int(nunota)
        except (ValueError, TypeError):
            return False, "NUNOTA inválido para atualização."

        faturado, _ = self.verificar_pedido_faturado(nunota)
        if faturado:
            return False, "Este pedido já foi faturado e gerou uma nota de venda, portanto não pode ser alterado."


        local_fields = {str(campo).upper(): {"$": str(valor)}}
        fields_to_save = [str(campo).upper()]

        if codusu is not None:
            try:
                codusu_int = int(codusu)
                local_fields["CODUSU"] = {"$": str(codusu_int)}
                if "CODUSU" not in fields_to_save:
                    fields_to_save.append("CODUSU")
                local_fields["CODUSUINC"] = {"$": str(codusu_int)}
                if "CODUSUINC" not in fields_to_save:
                    fields_to_save.append("CODUSUINC")
            except (ValueError, TypeError):
                pass

        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
        payload = {
            "serviceName": "CRUDServiceProvider.saveRecord",
            "requestBody": {
                "dataSet": {
                    "rootEntity": "CabecalhoNota",
                    "dataRow": {
                        "key": {"NUNOTA": {"$": str(nunota)}},
                        "localFields": local_fields
                    },
                    "entity": {"fieldset": {"list": ",".join(fields_to_save)}}
                }
            }
        }
        try:
            r = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200 and r.json().get("status") == "1":
                print(f"[Sankhya] Pedido #{nunota} campo {campo} atualizado para {valor} em TGFCAB!")
                if campo_upper == "VLRFRETE":
                    self._atualizar_total_cabecalho(nunota, codusu=codusu, jsessionid=session_to_use)
                if codusu:
                    self._atualizar_usuario_solicitante_pedido(nunota, codusu, jsessionid=session_to_use)
                return True, f"Campo {campo} atualizado com sucesso no Sankhya!"
            else:
                msg = r.json().get("statusMessage", "Erro ao atualizar campo em TGFCAB.")
                print(f"[Sankhya] Erro ao atualizar campo {campo}: {msg}")
                return False, msg
        except Exception as e:
            print(f"[Sankhya] Exceção ao atualizar campo {campo}: {e}")
            return False, str(e)

    def salvar_item_pedido(self, nunota, codprod, qtdneg, vlrunit, vlrtot=None, vlrdesc=0, percdesc=0, sequencia=None, codemp=1, codlocalorig=1000000, codvol='UN', codusu=None, jsessionid=None):
        """
        Insere ou atualiza um item na tabela TGFITE via Sankhya API (CRUDServiceProvider.saveRecord na entidade ItemNota).
        Se sequencia for None ou <= 0, calcula a próxima sequência para o NUNOTA.
        Grava o usuário logado (CODUSU) na TGFITE.
        Retorna: (sucesso: bool, sequencia: int | None, mensagem: str)
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            if not self.autenticar():
                return False, None, "Não foi possível autenticar na API do Sankhya."
            session_to_use = self.jsessionid

        try:
            nunota = int(nunota)
            codprod = int(codprod)
            qtdneg = float(qtdneg)
            vlrunit = float(vlrunit)
            vlrdesc = float(vlrdesc or 0)
            percdesc = float(percdesc or 0)
            codemp = int(codemp or 1)
            codlocalorig = int(codlocalorig or 1000000)
            codvol = str(codvol or "UN").strip()
        except (ValueError, TypeError) as e:
            return False, None, f"Parâmetros numéricos inválidos para salvar item: {e}"

        faturado, _ = self.verificar_pedido_faturado(nunota)
        if faturado:
            return False, None, "Este pedido já foi faturado e gerou uma nota de venda, portanto não pode receber novos itens ou alterações."

        if vlrtot is None or float(vlrtot) <= 0:
            vlrtot = max(0.0, (qtdneg * vlrunit) - vlrdesc)
        else:
            vlrtot = float(vlrtot)

        # Se sequência não foi informada ou for <= 0, busca a próxima disponível para este pedido
        if not sequencia or int(sequencia) <= 0:
            try:
                sql_seq = f"SELECT NVL(MAX(SEQUENCIA), 0) + 1 FROM TGFITE WHERE NUNOTA = {nunota}"
                res_seq = self._executar_sql(sql_seq)
                if res_seq and res_seq.get("status") == "1" and res_seq.get("responseBody", {}).get("rows"):
                    sequencia = int(res_seq["responseBody"]["rows"][0][0])
                else:
                    sequencia = 1
            except Exception as e_seq:
                print(f"[Sankhya] Erro ao obter próxima sequencia: {e_seq}")
                sequencia = 1
        else:
            sequencia = int(sequencia)

        # Consulta TGFTOP.ATUALEST para definir RESERVA e ATUALESTOQUE
        reserva_val = "N"
        atualestoque_val = "0"
        try:
            sql_top = f"""
            SELECT NVL(TOP.ATUALEST, 'N')
            FROM TGFCAB CAB
            JOIN TGFTOP TOP ON CAB.CODTIPOPER = TOP.CODTIPOPER AND CAB.DHTIPOPER = TOP.DHALTER
            WHERE CAB.NUNOTA = {nunota}
            """
            res_top = self._executar_sql(sql_top)
            if res_top and res_top.get("status") == "1" and res_top.get("responseBody", {}).get("rows"):
                atualest_top = str(res_top["responseBody"]["rows"][0][0] or "N").strip()
                if atualest_top == "R":
                    reserva_val = "S"
                    atualestoque_val = "0"
                elif atualest_top == "E":
                    reserva_val = "N"
                    atualestoque_val = "1"
                elif atualest_top == "B":
                    reserva_val = "N"
                    atualestoque_val = "-1"
                else:
                    reserva_val = "N"
                    atualestoque_val = "0"
        except Exception as e_top:
            print(f"[Sankhya] Erro ao consultar ATUALEST da TOP para o pedido #{nunota}: {e_top}")

        # Obtém CODPARC e CODEMP da TGFCAB para validar regras de promoção
        codparc_pedido = 0
        try:
            res_parc = self._executar_sql(f"SELECT NVL(CODPARC, 0), NVL(CODEMP, 1) FROM TGFCAB WHERE NUNOTA = {nunota}")
            if res_parc and res_parc.get("status") == "1" and res_parc.get("responseBody", {}).get("rows"):
                r_parc = res_parc["responseBody"]["rows"][0]
                codparc_pedido = int(r_parc[0] or 0)
                if not codemp or codemp == 1:
                    codemp = int(r_parc[1] or codemp or 1)
        except Exception as e_p:
            print(f"[Sankhya] Aviso ao buscar CODPARC para pedido #{nunota}: {e_p}")

        # Valida se o produto possui promoção ativa
        promo_val = self.obter_promocao_produto(codprod=codprod, codemp=codemp, codparc=codparc_pedido)
        vlrunit_salvar = vlrunit
        vlrdesc_salvar = vlrdesc
        percdesc_salvar = percdesc
        vlrtot_salvar = vlrtot

        if promo_val > 0:
            preco_liq_unit = round(vlrtot / qtdneg, 2) if qtdneg > 0 else promo_val
            if preco_liq_unit >= promo_val - 0.01:
                # O desconto dado ao valor cheio do produto atinge o valor líquido que é a promoção.
                # Não é desconto extra do vendedor, portanto para o Sankhya o valor unitário
                # é o preço promocional e o desconto gravado é ZERO (não pede liberação).
                vlrunit_salvar = preco_liq_unit
                vlrdesc_salvar = 0.0
                percdesc_salvar = 0.0
                vlrtot_salvar = round(preco_liq_unit * qtdneg, 2)
            else:
                # O vendedor concedeu mais desconto do que o aplicado na promoção.
                # Pede liberação apenas para o valor do desconto que excedeu a promoção.
                desc_extra_unit = round(promo_val - preco_liq_unit, 2)
                desc_extra_tot = round(desc_extra_unit * qtdneg, 2)
                desc_extra_perc = round((desc_extra_unit / promo_val) * 100, 2)
                vlrunit_salvar = promo_val
                vlrdesc_salvar = desc_extra_tot
                percdesc_salvar = desc_extra_perc
                vlrtot_salvar = round(preco_liq_unit * qtdneg, 2)

        local_fields = {
            "NUNOTA": {"$": str(nunota)},
            "SEQUENCIA": {"$": str(sequencia)},
            "CODEMP": {"$": str(codemp)},
            "CODPROD": {"$": str(codprod)},
            "CODLOCALORIG": {"$": str(codlocalorig)},
            "CODVOL": {"$": codvol},
            "QTDNEG": {"$": str(qtdneg)},
            "VLRUNIT": {"$": f"{vlrunit_salvar:.2f}"},
            "VLRTOT": {"$": f"{vlrtot_salvar:.2f}"},
            "VLRDESC": {"$": f"{vlrdesc_salvar:.2f}"},
            "PERCDESC": {"$": f"{percdesc_salvar:.2f}"},
            "USOPROD": {"$": "V"},
            "STATUSNOTA": {"$": "P"},
            "PENDENTE": {"$": "S"},
            "RESERVA": {"$": reserva_val},
            "ATUALESTOQUE": {"$": atualestoque_val}
        }

        field_list = [
            "NUNOTA", "SEQUENCIA", "CODEMP", "CODPROD", "CODLOCALORIG",
            "CODVOL", "QTDNEG", "VLRUNIT", "VLRTOT", "VLRDESC", "PERCDESC",
            "USOPROD", "STATUSNOTA", "PENDENTE", "RESERVA", "ATUALESTOQUE"
        ]

        if codusu is not None:
            try:
                codusu_int = int(codusu)
                local_fields["CODUSU"] = {"$": str(codusu_int)}
                field_list.append("CODUSU")
            except (ValueError, TypeError):
                pass

        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
        payload = {
            "serviceName": "CRUDServiceProvider.saveRecord",
            "requestBody": {
                "dataSet": {
                    "rootEntity": "ItemNota",
                    "includePresentationFields": "S",
                    "dataRow": {
                        "key": {
                            "NUNOTA": {"$": str(nunota)},
                            "SEQUENCIA": {"$": str(sequencia)}
                        },
                        "localFields": local_fields
                    },
                    "entity": {
                        "fieldset": {
                            "list": ",".join(field_list)
                        }
                    }
                }
            }
        }

        try:
            r = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200:
                dados = r.json()
                if dados.get("status") == "1":
                    print(f"[Sankhya] Item #{codprod} (Seq. {sequencia}) inserido com sucesso em TGFITE para o Pedido #{nunota}!")
                    
                    # Atualiza o total VLRNOTA na TGFCAB
                    self._atualizar_total_cabecalho(nunota, codusu=codusu, jsessionid=session_to_use)
                    if codusu:
                        self._atualizar_usuario_solicitante_pedido(nunota, codusu, jsessionid=session_to_use)
                    
                    return True, sequencia, f"Item inserido com sucesso (Seq. {sequencia})!"
                else:
                    msg_erro = dados.get("statusMessage", "Erro desconhecido ao gravar TGFITE.")
                    print(f"[Sankhya] Erro ao gravar TGFITE: {msg_erro}")
                    return False, None, msg_erro
            else:
                return False, None, f"Falha HTTP {r.status_code} ao comunicar com o Sankhya."
        except Exception as e:
            print(f"[Sankhya] Exceção ao gravar TGFITE: {e}")
            return False, None, str(e)

    def excluir_item_pedido(self, nunota, sequencia, codusu=None, jsessionid=None):
        """
        Remove um item da tabela TGFITE via Sankhya API (CRUDServiceProvider.removeRecord na entidade ItemNota).
        Retorna: (sucesso: bool, mensagem: str)
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            if not self.autenticar():
                return False, "Não foi possível autenticar na API do Sankhya."
            session_to_use = self.jsessionid

        try:
            nunota = int(nunota)
            sequencia = int(sequencia)
        except (ValueError, TypeError) as e:
            return False, f"Identificadores de pedido ou sequência inválidos: {e}"

        faturado, _ = self.verificar_pedido_faturado(nunota)
        if faturado:
            return False, "Este pedido já foi faturado e gerou uma nota de venda, portanto seus itens não podem ser excluídos."

        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.removeRecord&outputType=json&mgeSession={session_to_use}"
        payload = {
            "serviceName": "CRUDServiceProvider.removeRecord",
            "requestBody": {
                "entity": {
                    "rootEntity": "ItemNota",
                    "id": {
                        "NUNOTA": {"$": str(nunota)},
                        "SEQUENCIA": {"$": str(sequencia)}
                    }
                }
            }
        }

        try:
            r = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200:
                dados = r.json()
                if dados.get("status") == "1":
                    print(f"[Sankhya] Item Seq. {sequencia} removido com sucesso de TGFITE do Pedido #{nunota}!")
                    
                    # Atualiza o total VLRNOTA na TGFCAB
                    self._atualizar_total_cabecalho(nunota, codusu=codusu, jsessionid=session_to_use)
                    if codusu:
                        self._atualizar_usuario_solicitante_pedido(nunota, codusu, jsessionid=session_to_use)
                    
                    return True, "Item excluído com sucesso!"
                else:
                    msg_erro = dados.get("statusMessage", "Erro ao excluir item.")
                    print(f"[Sankhya] Erro ao excluir TGFITE: {msg_erro}")
                    return False, msg_erro
            else:
                return False, f"Falha HTTP {r.status_code} ao excluir item."
        except Exception as e:
            print(f"[Sankhya] Exceção ao excluir TGFITE: {e}")
            return False, str(e)

    def excluir_pedido(self, nunota, motivo=None, observacao=None):
        """
        Exclui permanentemente o pedido no banco de dados do Sankhya (TGFCAB, TGFITE, TSILIB, etc.):
        1. Verifica se o pedido já foi faturado (EXISTS em TGFVAR). Se faturado, bloqueia a exclusão.
        2. Se confirmado (STATUSNOTA = 'L') e não faturado, registra o motivo do cancelamento e prossegue.
        3. Remove primeiro todos os itens associados na TGFITE via CRUDServiceProvider.removeRecord (ItemNota) para liberar estoques.
        4. Remove o registro do cabeçalho da nota em TGFCAB via CRUDServiceProvider.removeRecord (CabecalhoNota).
        Retorna: (sucesso: bool, mensagem: str)
        """
        if not self.jsessionid:
            if not self.autenticar():
                return False, "Não foi possível autenticar na API do Sankhya."

        try:
            nunota = int(nunota)
        except (ValueError, TypeError) as e:
            return False, f"Número Único (NUNOTA) inválido: {e}"

        faturado, statusnota = self.verificar_pedido_faturado(nunota)
        if faturado:
            return False, "O pedido já foi faturado e gerou uma nota de venda, portanto não pode ser excluído."

        if statusnota == "L":
            log_motivo = f" Motivo: {motivo}" if motivo else ""
            log_obs = f" | Observações: {observacao}" if observacao else ""
            print(f"[Sankhya] Excluindo pedido confirmado #{nunota}.{log_motivo}{log_obs}")

        # 1. Remove primeiro todos os itens associados em TGFITE para liberar reservas de estoque
        try:
            sql_itens = f"SELECT SEQUENCIA FROM TGFITE WHERE NUNOTA = {nunota}"
            res_itens = self._executar_sql(sql_itens)
            if res_itens and res_itens.get("status") == "1" and res_itens.get("responseBody", {}).get("rows"):
                for row in res_itens["responseBody"]["rows"]:
                    seq_it = row[0]
                    # Exclui diretamente via CRUDServiceProvider para o item
                    url_it = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.removeRecord&outputType=json&mgeSession={self.jsessionid}"
                    payload_it = {
                        "serviceName": "CRUDServiceProvider.removeRecord",
                        "requestBody": {
                            "entity": {
                                "rootEntity": "ItemNota",
                                "id": {
                                    "NUNOTA": {"$": str(nunota)},
                                    "SEQUENCIA": {"$": str(seq_it)}
                                }
                            }
                        }
                    }
                    self.session.post(url_it, json=payload_it, headers={"Content-Type": "application/json"}, timeout=15)
        except Exception as e_it:
            print(f"[Sankhya] Aviso ao limpar itens pré-exclusão do pedido #{nunota}: {e_it}")

        # 2. Exclui permanentemente o registro de CabecalhoNota na TGFCAB
        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.removeRecord&outputType=json&mgeSession={self.jsessionid}"
        payload_cab = {
            "serviceName": "CRUDServiceProvider.removeRecord",
            "requestBody": {
                "entity": {
                    "rootEntity": "CabecalhoNota",
                    "id": {
                        "NUNOTA": {"$": str(nunota)}
                    }
                }
            }
        }

        try:
            r = self.session.post(url, json=payload_cab, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200:
                dados = r.json()
                if dados.get("status") == "1":
                    print(f"[Sankhya] Pedido #{nunota} excluído permanentemente da TGFCAB!")
                    return True, f"Pedido #{nunota} excluído com sucesso da base do Sankhya!"
                elif dados.get("status") == "3" or "Sessão" in str(dados.get("statusMessage", "")) or "expirada" in str(dados.get("statusMessage", "")):
                    print("[Sankhya] Sessão expirada na exclusão. Reautenticando...")
                    if self.autenticar():
                        url_retry = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.removeRecord&outputType=json&mgeSession={self.jsessionid}"
                        r_retry = self.session.post(url_retry, json=payload_cab, headers={"Content-Type": "application/json"}, timeout=15)
                        if r_retry.status_code == 200 and r_retry.json().get("status") == "1":
                            print(f"[Sankhya] Pedido #{nunota} excluído permanentemente da TGFCAB após retry!")
                            return True, f"Pedido #{nunota} excluído com sucesso da base do Sankhya!"
                        msg_erro = r_retry.json().get("statusMessage", "Erro ao excluir registro de TGFCAB.")
                        return False, msg_erro
                else:
                    msg_erro = dados.get("statusMessage", "Erro ao excluir registro de TGFCAB no Sankhya.")
                    print(f"[Sankhya] Erro ao excluir TGFCAB: {msg_erro}")
                    return False, msg_erro
            else:
                return False, f"Falha HTTP {r.status_code} ao excluir pedido no Sankhya."
        except Exception as e:
            print(f"[Sankhya] Exceção ao excluir TGFCAB: {e}")
            return False, str(e)

    def _atualizar_total_cabecalho(self, nunota, codusu=None, jsessionid=None):
        """
        Calcula a soma de VLRTOT dos itens em TGFITE + VLRFRETE na TGFCAB e atualiza o campo VLRNOTA na TGFCAB.
        """
        try:
            sql = f"""
            SELECT 
                NVL((SELECT NVL(SUM(VLRTOT), 0) FROM TGFITE WHERE NUNOTA = {nunota}), 0) + 
                NVL((SELECT NVL(VLRFRETE, 0) FROM TGFCAB WHERE NUNOTA = {nunota}), 0) AS TOTAL
            FROM DUAL
            """
            res = self._executar_sql(sql)
            if res and res.get("status") == "1":
                rows = res.get("responseBody", {}).get("rows", [])
                if rows and len(rows) > 0 and rows[0][0] is not None:
                    total = round(float(rows[0][0]), 2)
                    self.atualizar_campo_cabecalho(nunota, "VLRNOTA", total, codusu=codusu, jsessionid=jsessionid)
        except Exception as e:
            print(f"[Sankhya] Erro ao recalcular total do cabeçalho #{nunota}: {e}")

    def _atualizar_valores_item_tgfite(self, nunota, sequencia, vlrunit, vlrtot, vlrdesc, percdesc, jsessionid=None):
        """
        Atualiza os valores de VLRUNIT, VLRTOT, VLRDESC e PERCDESC de um item na TGFITE via Sankhya API.
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            self.autenticar()
            session_to_use = self.jsessionid
        try:
            url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
            payload = {
                "serviceName": "CRUDServiceProvider.saveRecord",
                "requestBody": {
                    "dataSet": {
                        "rootEntity": "ItemNota",
                        "dataRow": {
                            "key": {
                                "NUNOTA": {"$": str(nunota)},
                                "SEQUENCIA": {"$": str(sequencia)}
                            },
                            "localFields": {
                                "VLRUNIT": {"$": f"{float(vlrunit):.2f}"},
                                "VLRTOT": {"$": f"{float(vlrtot):.2f}"},
                                "VLRDESC": {"$": f"{float(vlrdesc):.2f}"},
                                "PERCDESC": {"$": f"{float(percdesc):.2f}"}
                            }
                        },
                        "entity": {
                            "fieldset": {
                                "list": "VLRUNIT,VLRTOT,VLRDESC,PERCDESC"
                            }
                        }
                    }
                }
            }
            self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        except Exception as e:
            print(f"[Sankhya] Erro ao atualizar item Seq. {sequencia} TGFITE #{nunota}: {e}")

    def _verificar_desconto_extra_pedido(self, nunota, codemp=1, codparc=0):
        """
        Verifica se algum item do pedido possui desconto concedido pelo vendedor que exceda a promoção.
        Retorna True se houver desconto adicional (exigindo liberação).
        Retorna False se todos os descontos forem promocionais ou não houver desconto.
        """
        try:
            sql = f"SELECT CODPROD, QTDNEG, VLRUNIT, VLRTOT, NVL(VLRDESC, 0) FROM TGFITE WHERE NUNOTA = {nunota}"
            res = self._executar_sql(sql)
            if not res or res.get("status") != "1":
                return False
            rows = res.get("responseBody", {}).get("rows", [])
            for r in rows:
                codprod = int(r[0])
                qtd = float(r[1] or 1)
                vlrunit = float(r[2] or 0)
                vlrtot = float(r[3] or 0)
                vlrdesc = float(r[4] or 0)

                if vlrdesc <= 0.01:
                    continue

                promo_val = self.obter_promocao_produto(codprod=codprod, codemp=codemp, codparc=codparc)
                if promo_val <= 0:
                    # Produto sem promoção e com desconto -> Exige liberação
                    return True

                unit_liq = round(vlrtot / qtd, 2) if qtd > 0 else promo_val
                # Se o preço líquido vendido for menor que o preço da promoção (com tolerância de 2 centavos), houve desconto extra!
                if unit_liq < promo_val - 0.02:
                    return True
            return False
        except Exception as e:
            print(f"[Sankhya] Erro ao verificar desconto extra: {e}")
            return False

    def obter_pedido_completo(self, nunota):
        """
        Consulta os dados completos de cabeçalho (TGFCAB) e itens (TGFITE) de um pedido por NUNOTA.
        Retorna: dict com cabecalho, itens, tabela_preco, limite_credito ou None.
        """
        if not self.jsessionid:
            self.autenticar()

        try:
            nunota = int(nunota)
        except (ValueError, TypeError):
            return None

        sql = f"""
        SELECT
            CAB.NUNOTA,
            NVL(CAB.NUMNOTA, 0) AS NUMNOTA,
            CAB.CODEMP,
            NVL(EMP.NOMEFANTASIA, EMP.RAZAOSOCIAL) AS NOMEEMP,
            CAB.CODPARC,
            NVL(PAR.RAZAOSOCIAL, PAR.NOMEPARC) AS NOMEPARC,
            CAB.CODTIPOPER,
            NVL(TOP.DESCROPER, '-') AS DESCROPER,
            CAB.CODTIPVENDA,
            NVL(TPV.DESCRTIPVENDA, '-') AS DESCRTIPVENDA,
            TO_CHAR(CAB.DTNEG, 'DD/MM/YYYY') AS DTNEG,
            CAB.CODVEND,
            NVL(VEN.APELIDO, '-') AS NOMEVEND,
            NVL(CAB.OBSERVACAO, '') AS OBSERVACAO,
            NVL(CAB.STATUSNOTA, 'P') AS STATUSNOTA,
            NVL(CAB.VLRNOTA, 0) AS VLRNOTA,
            NVL(CAB.CIF_FOB, 'C') AS CIF_FOB,
            NVL(CAB.VLRFRETE, 0) AS VLRFRETE,
            CASE WHEN EXISTS(SELECT 1 FROM TGFVAR VAR WHERE VAR.NUNOTA = CAB.NUNOTA OR VAR.NUNOTAORIG = CAB.NUNOTA) THEN 'S' ELSE 'N' END AS FATURADO
        FROM
            TGFCAB CAB
            LEFT JOIN TSIEMP EMP ON CAB.CODEMP = EMP.CODEMP
            LEFT JOIN TGFPAR PAR ON CAB.CODPARC = PAR.CODPARC
            LEFT JOIN (SELECT CODTIPOPER, DESCROPER FROM (SELECT CODTIPOPER, DESCROPER, ROW_NUMBER() OVER (PARTITION BY CODTIPOPER ORDER BY DHALTER DESC) RN FROM TGFTOP) WHERE RN = 1) TOP ON CAB.CODTIPOPER = TOP.CODTIPOPER
            LEFT JOIN (SELECT CODTIPVENDA, DESCRTIPVENDA FROM (SELECT CODTIPVENDA, DESCRTIPVENDA, ROW_NUMBER() OVER (PARTITION BY CODTIPVENDA ORDER BY DHALTER DESC) RN FROM TGFTPV) WHERE RN = 1) TPV ON CAB.CODTIPVENDA = TPV.CODTIPVENDA
            LEFT JOIN TGFVEN VEN ON CAB.CODVEND = VEN.CODVEND
        WHERE
            CAB.NUNOTA = {nunota}
        """

        res = self._executar_sql(sql)
        if not res or res.get("status") != "1":
            return None

        rows = res.get("responseBody", {}).get("rows", [])
        if not rows:
            return None

        r = rows[0]
        codparc = r[4]
        statusnota = str(r[14]).strip() if r[14] else "P"
        faturado_var = (str(r[18]).strip().upper() == "S") if len(r) > 18 and r[18] else False
        faturado = (statusnota == "L" and faturado_var)

        cabecalho = {
            "nunota": r[0],
            "numnota": r[1],
            "codemp": r[2],
            "nomeemp": str(r[3]).strip() if r[3] else "AUTOGIRO JP",
            "codparc": codparc,
            "nomeparc": str(r[5]).strip() if r[5] else "",
            "codtipoper": r[6],
            "descroper": str(r[7]).strip() if r[7] else "",
            "codtipvenda": r[8],
            "descrtipvenda": str(r[9]).strip() if r[9] else "",
            "dtneg": str(r[10]).strip() if r[10] else datetime.now().strftime("%d/%m/%Y"),
            "codvend": r[11],
            "nomevend": str(r[12]).strip() if r[12] else "",
            "observacao": str(r[13]).strip() if r[13] else "",
            "statusnota": statusnota,
            "vlrnota": float(r[15]) if r[15] is not None else 0.0,
            "cif_fob": str(r[16]).strip() if len(r) > 16 and r[16] else "C",
            "vlrfrete": float(r[17]) if len(r) > 17 and r[17] is not None else 0.0,
            "faturado": faturado,
            "pode_excluir": not faturado,
            "pode_alterar": not faturado
        }


        # Busca Tabela de Preço e Limite de Crédito associados ao parceiro
        tabela_preco = self.obter_tabela_preco_parceiro(codparc) if codparc else None
        limite_credito = self.obter_limite_credito_parceiro(codparc) if codparc else ""
        itens = self.listar_itens_pedido_venda(nunota)

        return {
            "cabecalho": cabecalho,
            "tabela_preco": tabela_preco,
            "limite_credito": limite_credito,
            "itens": itens
        }

    def listar_opcoes_frete(self):
        """
        Retorna as opções do campo CIF_FOB de TGFCAB através da consulta nas tabelas de dicionário TDDCAM / TDDOPC.
        Query:
        SELECT OPC.VALOR, OPC.OPCAO FROM TDDCAM CAM LEFT OUTER JOIN TDDOPC OPC ON CAM.NUCAMPO = OPC.NUCAMPO WHERE CAM.NOMETAB = 'TGFCAB' AND UPPER(CAM.NOMECAMPO) LIKE '%CIF_FOB%'
        """
        if not self.jsessionid:
            self.autenticar()

        sql = """
        SELECT
            OPC.VALOR,
            OPC.OPCAO
        FROM
            TDDCAM CAM
            LEFT OUTER JOIN TDDOPC OPC ON CAM.NUCAMPO = OPC.NUCAMPO
        WHERE
            CAM.NOMETAB = 'TGFCAB'
            AND UPPER(CAM.NOMECAMPO) LIKE '%CIF_FOB%'
        ORDER BY
            OPC.ORDEM, OPC.VALOR
        """
        VALORES_EXCLUIDOS = {"S", "T"}
        ORDEM_PREFERENCIAL = {
            "D": 1,  # 1 - Transp. Próprio Destinatário
            "R": 2,  # 2 - Transp. Próprio Remetente
            "C": 3,  # 3 - CIF
            "F": 4,  # 4 - FOB
        }

        res = self._executar_sql(sql)
        opcoes = []
        if res and res.get("status") == "1":
            rows = res.get("responseBody", {}).get("rows", [])
            for r in rows:
                if r[0] and r[1]:
                    valor = str(r[0]).strip().upper()
                    if valor not in VALORES_EXCLUIDOS:
                        opcoes.append({
                            "valor": valor,
                            "opcao": str(r[1]).strip()
                        })

        if not opcoes:
            opcoes = [
                {"valor": "D", "opcao": "Transp. Próprio Destinatário"},
                {"valor": "R", "opcao": "Transp. Próprio Remetente"},
                {"valor": "C", "opcao": "CIF - Contratação do Frete por conta do Remetente"},
                {"valor": "F", "opcao": "FOB - Contratação do Frete por conta do Destinatário"}
            ]
        else:
            opcoes.sort(key=lambda x: ORDEM_PREFERENCIAL.get(x["valor"], 99))

        self._opcoes_frete_cache = opcoes
        return opcoes

    # ==========================================
    # VALIDAÇÃO DE LIMITES E LIBERAÇÕES (TSILIB / SANKHYA OM)
    # ==========================================
    def consultar_limites_liberacoes(self, nunota, codparc=0, vlr_total=0.0, itens=None, codusu=None):
        """
        Consulta os dados financeiros do parceiro e lê diretamente da TSILIB todos os eventos de
        liberação gerados nativamente pelo Sankhya OM para o pedido (NUNOTA).
        """
        if not self.jsessionid:
            self.autenticar()

        nunota = int(nunota) if nunota else 0
        codparc = int(codparc) if codparc else 0
        vlr_total = float(vlr_total) if vlr_total else 0.0

        # Se nunota existir e codparc for 0, obtém da TGFCAB
        if nunota > 0 and (codparc == 0 or vlr_total <= 0):
            res_cab = self._executar_sql(f"SELECT CODPARC, NVL(VLRNOTA, 0) FROM TGFCAB WHERE NUNOTA = {nunota}")
            if res_cab and res_cab.get("status") == "1" and res_cab["responseBody"].get("rows"):
                r_cab = res_cab["responseBody"]["rows"][0]
                if codparc == 0:
                    codparc = int(r_cab[0] or 0)
                if vlr_total <= 0:
                    vlr_total = float(r_cab[1] or 0.0)

        # 1. Consulta Dados Financeiros do Parceiro (Limite, Utilizado, Vencidos, Bloqueio)
        fin_info = {
            "limcred": 0.0,
            "vlr_utilizado": 0.0,
            "saldo_credito": 0.0,
            "vlr_vencido": 0.0,
            "qtd_vencidos": 0,
            "bloqueio": "N",
            "motbloq": "",
            "nomeparc": "-"
        }
        if codparc > 0:
            sql_fin = f"""
            WITH LIMITE_UTILIZADO_CTE AS (
                SELECT
                    FIN.CODPARC,
                    NVL(SUM(FIN.VLRDESDOB), 0) AS VLR_UTILIZADO,
                    NVL(SUM(CASE WHEN FIN.DTVENC < TRUNC(SYSDATE) THEN FIN.VLRDESDOB ELSE 0 END), 0) AS VLR_VENCIDO,
                    COUNT(CASE WHEN FIN.DTVENC < TRUNC(SYSDATE) THEN 1 END) AS QTD_VENCIDOS
                FROM
                    TGFFIN FIN
                    INNER JOIN TGFTIT TIT ON FIN.CODTIPTIT = TIT.CODTIPTIT
                WHERE
                    FIN.RECDESP = 1
                    AND FIN.DHBAIXA IS NULL
                    AND FIN.PROVISAO = 'N'
                    AND TIT.SUBTIPOVENDA NOT IN (1, 10, 11)
                GROUP BY
                    FIN.CODPARC
            )
            SELECT
                COALESCE(PAR.LIMCRED, 0) AS LIMCRED,
                COALESCE(CTE.VLR_UTILIZADO, 0) AS VLR_UTILIZADO,
                (COALESCE(PAR.LIMCRED, 0) - COALESCE(CTE.VLR_UTILIZADO, 0)) AS SALDO_CREDITO,
                COALESCE(CTE.VLR_VENCIDO, 0) AS VLR_VENCIDO,
                COALESCE(CTE.QTD_VENCIDOS, 0) AS QTD_VENCIDOS,
                NVL(PAR.BLOQUEAR, 'N') AS BLOQUEIO,
                NVL(PAR.MOTBLOQ, '') AS MOTBLOQ,
                NVL(PAR.NOMEPARC, '-') AS NOMEPARC
            FROM
                TGFPAR PAR
                LEFT JOIN LIMITE_UTILIZADO_CTE CTE ON PAR.CODPARC = CTE.CODPARC
            WHERE
                PAR.CODPARC = {codparc}
            """
            res_fin = self._executar_sql(sql_fin)
            if res_fin and res_fin.get("status") == "1" and res_fin["responseBody"].get("rows"):
                r = res_fin["responseBody"]["rows"][0]
                fin_info["limcred"] = float(r[0]) if r[0] else 0.0
                fin_info["vlr_utilizado"] = float(r[1]) if r[1] else 0.0
                fin_info["saldo_credito"] = float(r[2]) if r[2] else 0.0
                fin_info["vlr_vencido"] = float(r[3]) if r[3] else 0.0
                fin_info["qtd_vencidos"] = int(r[4]) if r[4] else 0
                fin_info["bloqueio"] = str(r[5]).strip() if r[5] else "N"
                fin_info["motbloq"] = str(r[6]).strip() if len(r) > 6 and r[6] else ""
                fin_info["nomeparc"] = str(r[7]).strip() if len(r) > 7 and r[7] else "-"

        # 2. Consulta Liberações Registradas na TSILIB pelo motor nativo do Sankhya
        eventos = []
        precisa_liberacao = False

        if nunota > 0:
            vlr_nota_cab = vlr_total
            codemp_pedido = 1
            try:
                res_cab_tot = self._executar_sql(f"SELECT NVL(VLRNOTA, 0), NVL(CODEMP, 1) FROM TGFCAB WHERE NUNOTA = {nunota}")
                if res_cab_tot and res_cab_tot.get("status") == "1" and res_cab_tot.get("responseBody", {}).get("rows"):
                    r_ct = res_cab_tot["responseBody"]["rows"][0]
                    vlr_nota_cab = float(r_ct[0] or vlr_total)
                    codemp_pedido = int(r_ct[1] or 1)
            except Exception:
                pass
            if vlr_total <= 0:
                vlr_total = vlr_nota_cab

            sql_tsilib = f"""
            SELECT 
                L.EVENTO, 
                NVL(E.DESCRICAO, 'Evento de Liberação #' || L.EVENTO) AS DESCR_EVENTO,
                NVL(L.VLRLIMITE, 0) AS VLRLIMITE, 
                NVL(L.VLRATUAL, 0) AS VLRATUAL, 
                NVL(L.VLRLIBERADO, 0) AS VLRLIBERADO, 
                L.CODUSUSOLICIT, 
                NVL(U1.NOMEUSU, '-') AS SOLICITANTE, 
                TO_CHAR(L.DHSOLICIT, 'DD/MM/YYYY HH24:MI') AS DHSOLICIT, 
                L.CODUSULIB, 
                NVL(U2.NOMEUSU, '-') AS LIBERADOR, 
                TO_CHAR(L.DHLIB, 'DD/MM/YYYY HH24:MI') AS DHLIB, 
                NVL(L.REPROVADO, 'N') AS REPROVADO, 
                NVL(L.OBSERVACAO, '-') AS OBSERVACAO, 
                NVL(L.OBSLIB, '-') AS OBSLIB, 
                CASE 
                    WHEN L.DHLIB IS NOT NULL AND NVL(L.REPROVADO, 'N') = 'N' THEN 'L' 
                    WHEN L.DHLIB IS NOT NULL AND L.REPROVADO = 'S' THEN 'R' 
                    ELSE 'P' 
                END AS STATUS,
                L.SEQUENCIA,
                NVL(L.SEQCASCATA, 0) AS SEQCASCATA,
                NVL(L.NUCLL, 0) AS NUCLL
            FROM 
                TSILIB L 
                LEFT JOIN VGFLIBEVE E ON L.EVENTO = E.EVENTO
                LEFT JOIN TSIUSU U1 ON L.CODUSUSOLICIT = U1.CODUSU 
                LEFT JOIN TSIUSU U2 ON L.CODUSULIB = U2.CODUSU 
            WHERE 
                L.TABELA = 'TGFCAB' 
                AND L.NUCHAVE = {nunota}
            ORDER BY 
                L.SEQUENCIA, L.EVENTO
            """
            res_lib = self._executar_sql(sql_tsilib)
            if res_lib and res_lib.get("status") == "1" and res_lib["responseBody"].get("rows"):
                for row in res_lib["responseBody"]["rows"]:
                    evt_cod = int(row[0])
                    descr = str(row[1] or f"Evento #{evt_cod}")
                    v_lim = float(row[2] or 0.0)
                    v_at = float(row[3] or 0.0)
                    v_lib = float(row[4] or 0.0)
                    st = str(row[14] or "P")

                    # REGRA 1: Evento 13 (Valor Mínimo Tipo Negociação)
                    # Não deve exigir liberação se o valor total do pedido for maior ou igual ao limite da negociação
                    if evt_cod == 13:
                        if (vlr_total >= v_lim or vlr_nota_cab >= v_lim) and v_lim > 0:
                            continue

                    # REGRA 2: Evento 2 (Desconto Produto)
                    # Não deve exigir liberação se o produto estiver em promoção e o desconto concedido
                    # não ultrapassar o valor da promoção autorizada. Só exige se houver desconto adicional!
                    if evt_cod == 2:
                        tem_desc_extra = self._verificar_desconto_extra_pedido(nunota, codemp=codemp_pedido, codparc=codparc)
                        if not tem_desc_extra:
                            continue

                    if st == "P":
                        precisa_liberacao = True

                    eventos.append({
                        "evento": evt_cod,
                        "descricao": descr,
                        "vlrlimite": v_lim,
                        "vlratual": v_at,
                        "vlrliberado": v_lib,
                        "codususolicit": row[5],
                        "solicitante": str(row[6] or "-"),
                        "dhsolicit": str(row[7] or "-"),
                        "codusulib": row[8],
                        "liberador": str(row[9] or "-"),
                        "dhlib": str(row[10] or "-"),
                        "reprovado": str(row[11] or "N"),
                        "observacao": str(row[12] or "-") if row[12] else "-",
                        "obslib": str(row[13] or "-") if row[13] else "-",
                        "status": st,
                        "status_desc": "Liberada" if st == "L" else ("Recusada" if st == "R" else "Pendente"),
                        "sequencia": row[15],
                        "seqcascata": row[16],
                        "nucll": row[17]
                    })
        status_geral = "P" if precisa_liberacao else "L"

        return {
            "nunota": nunota,
            "codparc": codparc,
            "vlr_total": round(vlr_total, 2),
            "financeiro": fin_info,
            "eventos": eventos,
            "precisa_liberacao": precisa_liberacao,
            "status_geral": status_geral
        }

    def _atualizar_usuario_solicitante_pedido(self, nunota, codusu=None, jsessionid=None):
        """
        Garante que o usuário solicitante/inclusão (CODUSUINC em TGFCAB e CODUSUSOLICIT em TSILIB)
        seja gravado como o usuário logado (ex: EDUARDO.TAVARES / codusu=85) e não como o usuário
        de serviço da API (INTEGRA.API / codusu=145).
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            return

        try:
            nunota = int(nunota)
        except (ValueError, TypeError):
            return

        # Se codusu não foi informado, tenta descobrir a partir de CODUSUINC da TGFCAB
        if not codusu:
            try:
                sql_cab = f"SELECT CODUSUINC FROM TGFCAB WHERE NUNOTA = {nunota}"
                res_cab = self._executar_sql(sql_cab)
                if res_cab and res_cab.get("status") == "1" and res_cab.get("responseBody", {}).get("rows"):
                    codusu_val = res_cab["responseBody"]["rows"][0][0]
                    if codusu_val and int(codusu_val) not in (0, 145):
                        codusu = int(codusu_val)
            except Exception:
                pass

        if not codusu:
            return

        codusu = int(codusu)
        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"

        # 1. Atualiza CODUSUINC na TGFCAB
        try:
            payload_cab = {
                "serviceName": "CRUDServiceProvider.saveRecord",
                "requestBody": {
                    "dataSet": {
                        "rootEntity": "CabecalhoNota",
                        "dataRow": {
                            "key": {"NUNOTA": {"$": str(nunota)}},
                            "localFields": {
                                "CODUSUINC": {"$": str(codusu)}
                            }
                        },
                        "entity": {"fieldset": {"list": "CODUSUINC"}}
                    }
                }
            }
            self.session.post(url, json=payload_cab, headers={"Content-Type": "application/json"}, timeout=10)
        except Exception as e_cab:
            print(f"[Sankhya] Erro ao atualizar CODUSUINC em TGFCAB para #{nunota}: {e_cab}")

        # 2. Atualiza CODUSUSOLICIT em todas as linhas da TSILIB para este pedido
        try:
            sql_lib = f"""
            SELECT NUCHAVE, TABELA, EVENTO, SEQUENCIA, NVL(SEQCASCATA, 0), NVL(NUCLL, 0), CODUSUSOLICIT
            FROM TSILIB
            WHERE NUCHAVE = {nunota} AND TABELA = 'TGFCAB'
            """
            res_lib = self._executar_sql(sql_lib)
            if res_lib and res_lib.get("status") == "1" and res_lib.get("responseBody", {}).get("rows"):
                for r in res_lib["responseBody"]["rows"]:
                    evt = r[2]
                    seq = r[3]
                    seqcascata = r[4]
                    nucll = r[5]
                    cod_solicit_atual = int(r[6] or 0)
                    if cod_solicit_atual != codusu:
                        payload_lib = {
                            "serviceName": "CRUDServiceProvider.saveRecord",
                            "requestBody": {
                                "dataSet": {
                                    "rootEntity": "LiberacaoLimite",
                                    "dataRow": {
                                        "key": {
                                            "NUCHAVE": {"$": str(nunota)},
                                            "TABELA": {"$": "TGFCAB"},
                                            "EVENTO": {"$": str(evt)},
                                            "SEQUENCIA": {"$": str(seq)},
                                            "SEQCASCATA": {"$": str(seqcascata)},
                                            "NUCLL": {"$": str(nucll)}
                                        },
                                        "localFields": {
                                            "CODUSUSOLICIT": {"$": str(codusu)}
                                        }
                                    },
                                    "entity": {
                                        "fieldset": {
                                            "list": "CODUSUSOLICIT"
                                        }
                                    }
                                }
                            }
                        }
                        self.session.post(url, json=payload_lib, headers={"Content-Type": "application/json"}, timeout=10)
        except Exception as e_lib:
            print(f"[Sankhya] Erro ao atualizar CODUSUSOLICIT em TSILIB para #{nunota}: {e_lib}")

    def solicitar_liberacao_limites(self, nunota, codparc=0, vlr_total=0.0, motivo=None, codusu=1, jsessionid=None):
        """
        Atualiza o motivo da solicitação de liberação e solicitante para os eventos pendentes na TSILIB.
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            self.autenticar()
            session_to_use = self.jsessionid

        nunota = int(nunota)
        codusu = int(codusu) if codusu else 1
        motivo_texto = (motivo or "Solicitação de liberação via Central de Pedidos")
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        # 1. Consulta eventos pendentes na TSILIB
        sql_tsilib = f"""
        SELECT 
            L.EVENTO, 
            L.SEQUENCIA, 
            NVL(L.SEQCASCATA, 0) AS SEQCASCATA, 
            NVL(L.NUCLL, 0) AS NUCLL
        FROM 
            TSILIB L 
        WHERE 
            L.TABELA = 'TGFCAB' 
            AND L.NUCHAVE = {nunota}
            AND L.DHLIB IS NULL
        """
        res = self._executar_sql(sql_tsilib)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                evt = row[0]
                seq = row[1]
                seqcascata = row[2]
                nucll = row[3]

                url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
                payload = {
                    "serviceName": "CRUDServiceProvider.saveRecord",
                    "requestBody": {
                        "dataSet": {
                            "rootEntity": "LiberacaoLimite",
                            "dataRow": {
                                "key": {
                                    "NUCHAVE": {"$": str(nunota)},
                                    "TABELA": {"$": "TGFCAB"},
                                    "EVENTO": {"$": str(evt)},
                                    "SEQUENCIA": {"$": str(seq)},
                                    "SEQCASCATA": {"$": str(seqcascata)},
                                    "NUCLL": {"$": str(nucll)}
                                },
                                "localFields": {
                                    "CODUSUSOLICIT": {"$": str(codusu)},
                                    "DHSOLICIT": {"$": now_str},
                                    "OBSERVACAO": {"$": motivo_texto},
                                    "REPROVADO": {"$": "N"}
                                }
                            },
                            "entity": {
                                "fieldset": {
                                    "list": "CODUSUSOLICIT,DHSOLICIT,OBSERVACAO,REPROVADO"
                                }
                            }
                        }
                    }
                }
                try:
                    self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
                except Exception as e:
                    print(f"[Sankhya] Erro ao salvar solicitação de liberação evento {evt}: {e}")

        self._atualizar_usuario_solicitante_pedido(nunota, codusu, jsessionid=session_to_use)
        return True, "Solicitação de liberação registrada com sucesso no Sankhya!"

    def listar_usuarios_liberadores(self, busca=None, evento=None):
        """
        Lista usuários cadastrados na TSIUSU com indicador se possuem alçada cadastrada na TSILIM para o evento informado.
        Oculta e desconsidera o usuário 0 (SUP).
        """
        if not self.jsessionid:
            self.autenticar()

        where_clauses = ["U.CODUSU <> 0", "UPPER(U.NOMEUSU) <> 'SUP'"]
        if busca:
            busca_clean = str(busca).strip().replace("'", "''")
            if busca_clean.isdigit():
                where_clauses.append(f"(U.CODUSU = {busca_clean} OR UPPER(U.NOMEUSU) LIKE '%{busca_clean.upper()}%')")
            else:
                where_clauses.append(f"(UPPER(U.NOMEUSU) LIKE '%{busca_clean.upper()}%')")

        where_sql = "WHERE " + " AND ".join(where_clauses)

        alcada_condition = f"AND L.EVENTO = {int(evento)}" if (evento and int(evento) > 0) else ""

        sql = f"""
        SELECT 
            U.CODUSU, 
            U.NOMEUSU, 
            NVL(U.EMAIL, '') AS EMAIL,
            CASE WHEN EXISTS (
                SELECT 1 FROM TSILIM L 
                WHERE (L.CODUSU = U.CODUSU OR (L.CODGRU = U.CODGRUPO AND L.CODGRU > 0))
                {alcada_condition}
            ) THEN 'S' ELSE 'N' END AS TEM_ALCADA
        FROM 
            TSIUSU U
        {where_sql}
        ORDER BY 
            TEM_ALCADA DESC, U.NOMEUSU ASC
        """
        res = self._executar_sql(sql)
        usuarios = []
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for r in res["responseBody"]["rows"]:
                tem_alc = str(r[3] or "N").strip().upper() == "S"
                usuarios.append({
                    "codusu": int(r[0]),
                    "nomeusu": str(r[1] or ""),
                    "email": str(r[2] or ""),
                    "tem_alcada": tem_alc
                })
        return usuarios

    def verificar_alcada_usuario(self, codusu, evento):
        """
        Verifica se um usuário específico possui alçada para um evento na TSILIM.
        O usuário 0 (SUP) não possui alçada para seleção.
        """
        if not codusu or int(codusu) == 0:
            return False
        if not evento:
            return False

        if not self.jsessionid:
            self.autenticar()

        sql = f"""
        SELECT COUNT(*)
        FROM TSIUSU U
        WHERE U.CODUSU = {int(codusu)}
          AND U.CODUSU <> 0
          AND UPPER(U.NOMEUSU) <> 'SUP'
          AND EXISTS (
              SELECT 1 FROM TSILIM L
              WHERE L.EVENTO = {int(evento)}
                AND (L.CODUSU = U.CODUSU OR (L.CODGRU = U.CODGRUPO AND L.CODGRU > 0))
          )
        """
        res = self._executar_sql(sql)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            return int(res["responseBody"]["rows"][0][0]) > 0
        return False


    def definir_liberador_eventos(self, nunota, codusu_liberador=None, eventos=None, liberadores=None, codusu_solicitante=None, jsessionid=None):
        """
        Define o usuário liberador (CODUSULIB) para os eventos pendentes na TSILIB via CRUDServiceProvider.saveRecord na entidade LiberacaoLimite.
        Suporta liberador único ou liberadores específicos por evento via `liberadores` = [{'evento': 13, 'codusulib': 90}, ...]
        Retorna: (sucesso: bool, mensagem: str)
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            self.autenticar()
            session_to_use = self.jsessionid

        nunota = int(nunota)
        mapa_liberadores = {}
        if liberadores:
            if isinstance(liberadores, list):
                for item in liberadores:
                    if isinstance(item, dict) and "evento" in item and "codusulib" in item:
                        mapa_liberadores[int(item["evento"])] = int(item["codusulib"])
            elif isinstance(liberadores, dict):
                for k, v in liberadores.items():
                    mapa_liberadores[int(k)] = int(v)

        # 1. Consulta eventos pendentes na TSILIB
        sql_tsilib = f"""
        SELECT 
            L.EVENTO, 
            L.SEQUENCIA, 
            NVL(L.SEQCASCATA, 0) AS SEQCASCATA, 
            NVL(L.NUCLL, 0) AS NUCLL,
            NVL(L.CODUSULIB, 0) AS CODUSULIB
        FROM 
            TSILIB L 
        WHERE 
            L.TABELA = 'TGFCAB' 
            AND L.NUCHAVE = {nunota}
            AND L.DHLIB IS NULL
        """
        res = self._executar_sql(sql_tsilib)
        if not (res and res.get("status") == "1" and res["responseBody"].get("rows")):
            return True, "Não há eventos pendentes de liberação para este pedido."

        eventos_filtrados = res["responseBody"]["rows"]
        if eventos:
            evts_int = [int(e) for e in eventos]
            eventos_filtrados = [r for r in eventos_filtrados if int(r[0]) in evts_int]

        for row in eventos_filtrados:
            evt = int(row[0])
            seq = row[1]
            seqcascata = row[2]
            nucll = row[3]
            cod_atual = int(row[4] or 0)

            # Determina o liberador correspondente a este evento
            cod_lib = mapa_liberadores.get(evt)
            if cod_lib is None and codusu_liberador:
                cod_lib = int(codusu_liberador)
            if cod_lib is None:
                cod_lib = cod_atual

            if cod_lib and int(cod_lib) > 0:
                if not self.verificar_alcada_usuario(cod_lib, evt):
                    return False, f"O usuário #{cod_lib} não possui alçada para o Evento #{evt} no Sankhya."

            local_fields_lib = {
                "CODUSULIB": {"$": str(cod_lib)}
            }
            fieldset_list = ["CODUSULIB"]

            if codusu_solicitante:
                local_fields_lib["CODUSUSOLICIT"] = {"$": str(codusu_solicitante)}
                fieldset_list.append("CODUSUSOLICIT")

            url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
            payload = {
                "serviceName": "CRUDServiceProvider.saveRecord",
                "requestBody": {
                    "dataSet": {
                        "rootEntity": "LiberacaoLimite",
                        "dataRow": {
                            "key": {
                                "NUCHAVE": {"$": str(nunota)},
                                "TABELA": {"$": "TGFCAB"},
                                "EVENTO": {"$": str(evt)},
                                "SEQUENCIA": {"$": str(seq)},
                                "SEQCASCATA": {"$": str(seqcascata)},
                                "NUCLL": {"$": str(nucll)}
                            },
                            "localFields": local_fields_lib
                        },
                        "entity": {
                            "fieldset": {
                                "list": ",".join(fieldset_list)
                            }
                        }
                    }
                }
            }
            try:
                r = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
                if r.status_code == 200:
                    res_save = r.json()
                    if res_save.get("status") != "1":
                        msg_err = res_save.get("statusMessage", "Erro ao definir liberador")
                        return False, msg_err
                else:
                    return False, f"Falha HTTP {r.status_code} ao salvar liberador no Sankhya."
            except Exception as e:
                return False, f"Exceção ao comunicar com Sankhya: {str(e)}"

        if codusu_solicitante:
            self._atualizar_usuario_solicitante_pedido(nunota, codusu_solicitante, jsessionid=session_to_use)

        return True, "Liberador(es) definidos com sucesso!"

    def liberar_limites_pedido(self, nunota, observacao_lib=None, codusu_liberador=1, jsessionid=None):
        """
        Aprova e libera os eventos pendentes na TSILIB utilizando a entidade LiberacaoLimite
        e reexecuta a confirmação nativa do Sankhya (CACSP.confirmarNota).
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            self.autenticar()
            session_to_use = self.jsessionid

        nunota = int(nunota)
        codusu_lib = int(codusu_liberador) if codusu_liberador else 1
        obs = observacao_lib or "Liberação autorizada via Central de Pedidos"
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        # 1. Consulta eventos pendentes na TSILIB
        sql_tsilib = f"""
        SELECT 
            L.EVENTO, 
            L.SEQUENCIA, 
            NVL(L.SEQCASCATA, 0) AS SEQCASCATA, 
            NVL(L.NUCLL, 0) AS NUCLL,
            NVL(L.VLRATUAL, 0) AS VLRATUAL
        FROM 
            TSILIB L 
        WHERE 
            L.TABELA = 'TGFCAB' 
            AND L.NUCHAVE = {nunota}
            AND L.DHLIB IS NULL
        """
        res = self._executar_sql(sql_tsilib)
        if res and res.get("status") == "1" and res["responseBody"].get("rows"):
            for row in res["responseBody"]["rows"]:
                evt = row[0]
                seq = row[1]
                seqcascata = row[2]
                nucll = row[3]
                vlratual = row[4]

                # Salva via CRUDServiceProvider.saveRecord na entidade LiberacaoLimite
                url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
                payload = {
                    "serviceName": "CRUDServiceProvider.saveRecord",
                    "requestBody": {
                        "dataSet": {
                            "rootEntity": "LiberacaoLimite",
                            "dataRow": {
                                "key": {
                                    "NUCHAVE": {"$": str(nunota)},
                                    "TABELA": {"$": "TGFCAB"},
                                    "EVENTO": {"$": str(evt)},
                                    "SEQUENCIA": {"$": str(seq)},
                                    "SEQCASCATA": {"$": str(seqcascata)},
                                    "NUCLL": {"$": str(nucll)}
                                },
                                "localFields": {
                                    "CODUSULIB": {"$": str(codusu_lib)},
                                    "DHLIB": {"$": now_str},
                                    "VLRLIB": {"$": str(vlratual)},
                                    "OBSERVACAO": {"$": obs},
                                    "REPROVADO": {"$": "N"}
                                }
                            },
                            "entity": {
                                "fieldset": {
                                    "list": "CODUSULIB,DHLIB,VLRLIB,OBSERVACAO,REPROVADO"
                                }
                            }
                        }
                    }
                }
                try:
                    r = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
                    if r.status_code == 200:
                        res_save = r.json()
                        if res_save.get("status") != "1":
                            msg_err = res_save.get("statusMessage", "Erro ao salvar liberação")
                            return False, msg_err
                    else:
                        return False, f"Erro HTTP {r.status_code} ao salvar liberação no Sankhya."
                except Exception as e:
                    return False, f"Exceção ao comunicar com Sankhya: {str(e)}"

        # 2. Após registrar as liberações, aciona a confirmação nativa Java
        confirmado, msg_java, _ = self._confirmar_nota_java(nunota, jsessionid=session_to_use)
        if confirmado:
            return True, "Limites liberados e pedido confirmado com sucesso no Sankhya!"
        else:
            # Verifica se ainda há pendências de outras alçadas
            val = self.consultar_limites_liberacoes(nunota)
            if val.get("precisa_liberacao"):
                return True, "Liberação registrada com sucesso. Aguardando liberação das demais alçadas."
            return True, f"Liberação registrada. Retorno da confirmação: {msg_java}"

    def fechar_pedido_sankhya(self, nunota, codemp=1, codparc=0, vlr_total=0.0, itens=None, codusu=1, jsessionid=None):
        """
        Executa o fechamento e confirmação do pedido acionando o motor Java nativo do Sankhya (CACSP.confirmarNota).
        Todas as validações comerciais, financeiras e fiscais são executadas nativamente pelo Sankhya OM:
        - Inadimplência / Títulos vencidos
        - Limite de crédito do cliente
        - Descontos e Acréscimos
        - Margens e Lucratividade
        - Valor Mínimo por Negociação
        - Bloqueios no parceiro
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            self.autenticar()
            session_to_use = self.jsessionid

        nunota = int(nunota)

        # Validação obrigatória de itens: não pode fechar pedido sem pelo menos 1 item salvo
        if not itens:
            itens = self.listar_itens_pedido_venda(nunota)
        if not itens or len(itens) == 0:
            return {
                "sucesso": False,
                "precisa_liberacao": False,
                "status": "P",
                "mensagem": "Não é possível fechar o pedido: é obrigatório ter pelo menos 1 item adicionado e salvo.",
                "validacao": None
            }

        # PASSO 0: Sincroniza RESERVA e ATUALESTOQUE em TGFITE de acordo com TGFTOP.ATUALEST
        try:
            sql_top = f"""
            SELECT NVL(TOP.ATUALEST, 'N')
            FROM TGFCAB CAB
            JOIN TGFTOP TOP ON CAB.CODTIPOPER = TOP.CODTIPOPER AND CAB.DHTIPOPER = TOP.DHALTER
            WHERE CAB.NUNOTA = {nunota}
            """
            res_top = self._executar_sql(sql_top)
            atualest_top = "N"
            if res_top and res_top.get("status") == "1" and res_top.get("responseBody", {}).get("rows"):
                atualest_top = str(res_top["responseBody"]["rows"][0][0] or "N").strip()

            target_reserva = "S" if atualest_top == "R" else "N"
            target_atualestoque = "0" if atualest_top in ("R", "N") else ("1" if atualest_top == "E" else "-1")

            # Verifica se existem itens na TGFITE com valores divergentes
            sql_ite = f"""
            SELECT SEQUENCIA, NVL(RESERVA, 'X'), NVL(ATUALESTOQUE, -99)
            FROM TGFITE
            WHERE NUNOTA = {nunota}
            """
            res_ite = self._executar_sql(sql_ite)
            if res_ite and res_ite.get("status") == "1" and res_ite.get("responseBody", {}).get("rows"):
                for row_ite in res_ite["responseBody"]["rows"]:
                    seq_ite = row_ite[0]
                    res_atual = str(row_ite[1] or "X").strip()
                    est_atual = str(row_ite[2] if row_ite[2] is not None else "-99")

                    if res_atual != target_reserva or est_atual != target_atualestoque:
                        url_save = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
                        payload_ite = {
                            "serviceName": "CRUDServiceProvider.saveRecord",
                            "requestBody": {
                                "dataSet": {
                                    "rootEntity": "ItemNota",
                                    "dataRow": {
                                        "key": {
                                            "NUNOTA": {"$": str(nunota)},
                                            "SEQUENCIA": {"$": str(seq_ite)}
                                        },
                                        "localFields": {
                                            "RESERVA": {"$": target_reserva},
                                            "ATUALESTOQUE": {"$": target_atualestoque}
                                        }
                                    },
                                    "entity": {
                                        "fieldset": {
                                            "list": "RESERVA,ATUALESTOQUE"
                                        }
                                    }
                                }
                            }
                        }
                        r_save = self.session.post(url_save, json=payload_ite, headers={"Content-Type": "application/json"}, timeout=15)
                        if r_save.status_code != 200:
                            if self.autenticar():
                                session_to_use = self.jsessionid
                                url_save_retry = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={session_to_use}"
                                self.session.post(url_save_retry, json=payload_ite, headers={"Content-Type": "application/json"}, timeout=15)
        except Exception as e_sync:
            print(f"[Sankhya] Erro ao sincronizar RESERVA dos itens para #{nunota}: {e_sync}")

        # PASSO 0.1: Obtém CODEMP e CODPARC atualizados de TGFCAB
        if nunota > 0 and (not codparc or not codemp or codemp == 1):
            try:
                res_cab_info = self._executar_sql(f"SELECT NVL(CODEMP, 1), NVL(CODPARC, 0) FROM TGFCAB WHERE NUNOTA = {nunota}")
                if res_cab_info and res_cab_info.get("status") == "1" and res_cab_info.get("responseBody", {}).get("rows"):
                    row_ci = res_cab_info["responseBody"]["rows"][0]
                    codemp = int(row_ci[0] or codemp or 1)
                    codparc = int(row_ci[1] or codparc or 0)
            except Exception as e_ci:
                print(f"[Sankhya] Erro ao obter dados do cabecalho para fechamento #{nunota}: {e_ci}")

        # PASSO 0.2: Trata itens com promoção na TGFITE para evitar liberação indevida de desconto
        try:
            sql_itens_chk = f"SELECT SEQUENCIA, CODPROD, QTDNEG, VLRUNIT, VLRTOT, NVL(VLRDESC, 0), NVL(PERCDESC, 0) FROM TGFITE WHERE NUNOTA = {nunota}"
            res_ite_chk = self._executar_sql(sql_itens_chk)
            if res_ite_chk and res_ite_chk.get("status") == "1" and res_ite_chk.get("responseBody", {}).get("rows"):
                for row_p in res_ite_chk["responseBody"]["rows"]:
                    seq_p = row_p[0]
                    codp = int(row_p[1])
                    q_p = float(row_p[2] or 1)
                    u_p = float(row_p[3] or 0)
                    tot_p = float(row_p[4] or 0)
                    desc_p = float(row_p[5] or 0)

                    promo_p = self.obter_promocao_produto(codprod=codp, codemp=codemp, codparc=codparc)
                    if promo_p > 0:
                        unit_liq_p = round(tot_p / q_p, 2) if q_p > 0 else promo_p
                        if unit_liq_p >= promo_p - 0.01:
                            # O desconto concedido chega ao valor líquido da promoção autorizada.
                            # Para o Sankhya não exigir liberação, grava o preço promocional como unitário e desconto ZERO.
                            if desc_p > 0 or abs(u_p - unit_liq_p) > 0.01:
                                self._atualizar_valores_item_tgfite(nunota, seq_p, unit_liq_p, tot_p, 0.0, 0.0, session_to_use)
                        else:
                            # O vendedor concedeu mais desconto do que o aplicado na promoção.
                            # Exige liberação apenas para o desconto que excedeu a promoção.
                            extra_u = round(promo_p - unit_liq_p, 2)
                            extra_tot = round(extra_u * q_p, 2)
                            extra_perc = round((extra_u / promo_p) * 100, 2)
                            if abs(desc_p - extra_tot) > 0.01 or abs(u_p - promo_p) > 0.01:
                                self._atualizar_valores_item_tgfite(nunota, seq_p, promo_p, tot_p, extra_tot, extra_perc, session_to_use)
        except Exception as e_promo_sync:
            print(f"[Sankhya] Erro ao sincronizar itens promocionais no fechamento #{nunota}: {e_promo_sync}")

        # PASSO 0.3: Atualiza total geral na TGFCAB (VLRNOTA = Produtos + Frete)
        self._atualizar_total_cabecalho(nunota, codusu=codusu, jsessionid=session_to_use)

        # PASSO 1: Executa o serviço Java nativo de confirmação do Sankhya OM
        confirmado, msg_java, resp_dados = self._confirmar_nota_java(nunota, jsessionid=session_to_use)

        # Garante que o solicitante gravado na TGFCAB e TSILIB seja o usuário logado (ex: EDUARDO.TAVARES / codusu=85)
        if codusu:
            self._atualizar_usuario_solicitante_pedido(nunota, codusu, jsessionid=session_to_use)

        if confirmado:
            return {
                "sucesso": True,
                "precisa_liberacao": False,
                "status": "L",
                "mensagem": "Pedido confirmado com sucesso pelo motor nativo do Sankhya!",
                "validacao": None
            }

        # PASSO 2: Consulta os limites e eventos gerados na TSILIB
        val = self.consultar_limites_liberacoes(nunota, codparc, vlr_total, itens=itens, codusu=codusu)
        if val["precisa_liberacao"]:
            return {
                "sucesso": False,
                "precisa_liberacao": True,
                "status": "P",
                "mensagem": "O pedido possui pendências de liberação de limites geradas pelo Sankhya OM.",
                "validacao": val
            }

        # PASSO 3: Erro impeditivo de validação (ex: regra de TOP, campos obrigatórios, etc.)
        return {
            "sucesso": False,
            "precisa_liberacao": False,
            "status": "P",
            "mensagem": msg_java,
            "validacao": val
        }

    def _confirmar_nota_java(self, nunota, jsessionid=None):
        """
        Aciona o serviço Java nativo do Sankhya OM (CACSP.confirmarNota) no módulo mgecom.
        Executa TODAS as validações nativas do Sankhya:
        - Inadimplência / Títulos vencidos (Evento 2)
        - Limite de crédito do parceiro (Evento 3)
        - Descontos e Acréscimos (Evento 4 / 6)
        - Margem de Lucro e Rentabilidade (Evento 9 / 68)
        - Valor Mínimo por Negociação (Evento 13)
        - Bloqueio comercial de cadastro (Evento 1000 / TGFPAR)
        - Todas as demais alçadas configuradas em TGFLIBEVE / TSILIM.

        Se houver pendências de limite, o Sankhya registra automaticamente na TSILIB e mantém a nota não confirmada (STATUSNOTA = 'P', PENDENTE = 'S').
        Se não houver pendências ou estiver tudo liberado, confirma a nota (STATUSNOTA = 'L', PENDENTE = 'N').

        Retorna: (confirmado: bool, mensagem: str, dados_resposta: dict)
        """
        session_to_use = jsessionid or self.jsessionid
        if not session_to_use:
            if not self.autenticar():
                return False, "Falha de autenticação no Sankhya", None
            session_to_use = self.jsessionid

        payload = {
            "serviceName": "CACSP.confirmarNota",
            "requestBody": {
                "nota": {
                    "NUNOTA": {"$": str(nunota)}
                }
            }
        }

        def _enviar_requisicao(sess):
            url = f"{self.base_url}/mgecom/service.sbr?serviceName=CACSP.confirmarNota&outputType=json&mgeSession={sess}"
            return self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=45)

        try:
            r = _enviar_requisicao(session_to_use)

            # Se a requisição falhou (ex: 500 devido a sessão expirada no Wildfly, 401, 403) ou retornou status 3 (Sessão Expirada),
            # reautentica no Sankhya e tenta novamente com a nova sessão válida.
            precisa_reautenticar = False
            if r.status_code != 200:
                precisa_reautenticar = True
            else:
                try:
                    chk_dados = r.json()
                    status_str = str(chk_dados.get("status", "0"))
                    msg_chk = str(chk_dados.get("statusMessage", "")).lower()
                    if status_str == "3" or "sessão" in msg_chk or "expirada" in msg_chk or "invalid session" in msg_chk:
                        precisa_reautenticar = True
                except Exception:
                    pass

            if precisa_reautenticar:
                print(f"[Sankhya] Sessão expirada ou falha HTTP {r.status_code} na confirmação do pedido #{nunota}. Reautenticando no Sankhya...")
                if self.autenticar():
                    session_to_use = self.jsessionid
                    r = _enviar_requisicao(session_to_use)

            if r.status_code == 200:
                dados = r.json()
                status = dados.get("status", "0")
                if status == "1":
                    # Verifica status da nota no banco após a execução do serviço Java
                    sql_check = f"SELECT STATUSNOTA, PENDENTE FROM TGFCAB WHERE NUNOTA = {nunota}"
                    res_chk = self._executar_sql(sql_check)
                    status_nota = "P"
                    pendente = "S"
                    if res_chk and res_chk.get("status") == "1" and res_chk["responseBody"].get("rows"):
                        status_nota = str(res_chk["responseBody"]["rows"][0][0] or "P").strip()
                        pendente = str(res_chk["responseBody"]["rows"][0][1] or "S").strip()

                    # Verifica se há pendências na TSILIB
                    sql_lib = f"SELECT COUNT(1) FROM TSILIB WHERE NUCHAVE = {nunota} AND TABELA = 'TGFCAB' AND DHLIB IS NULL AND NVL(REPROVADO, 'N') = 'N'"
                    res_lib = self._executar_sql(sql_lib)
                    tem_lib_pendente = False
                    if res_lib and res_lib.get("status") == "1" and res_lib["responseBody"].get("rows"):
                        tem_lib_pendente = int(res_lib["responseBody"]["rows"][0][0] or 0) > 0

                    if status_nota == "L" and not tem_lib_pendente:
                        print(f"[Sankhya] Nota #{nunota} confirmada com sucesso via CACSP.confirmarNota (STATUSNOTA = L)!")
                        return True, "Pedido confirmado com sucesso pelo motor nativo do Sankhya!", dados
                    else:
                        print(f"[Sankhya] Nota #{nunota} executou validação via CACSP.confirmarNota. Possui pendências de liberação na TSILIB (STATUSNOTA = {status_nota}, PENDENTE = {pendente}).")
                        return False, "Pendência de liberação de limites gerada pelo Sankhya OM.", dados
                else:
                    msg = dados.get("statusMessage", "Erro na confirmação nativa do Sankhya")
                    print(f"[Sankhya] CACSP.confirmarNota retornou erro para #{nunota}: {msg}")
                    return False, msg, dados
            else:
                # Extrai mensagem tratada e legível caso persista erro HTTP do servidor Sankhya
                err_msg = ""
                try:
                    err_json = r.json()
                    err_msg = err_json.get("statusMessage") or err_json.get("error") or ""
                except Exception:
                    pass

                if not err_msg and r.text:
                    import re
                    match = re.search(r'<statusMessage>(.*?)</statusMessage>', r.text, re.DOTALL | re.IGNORECASE)
                    if match:
                        err_msg = match.group(1).strip()
                    elif "<body" in r.text.lower():
                        match_body = re.search(r'<body[^>]*>(.*?)</body>', r.text, re.DOTALL | re.IGNORECASE)
                        if match_body:
                            clean_text = re.sub(r'<[^>]+>', ' ', match_body.group(1)).strip()
                            if clean_text and "Internal Server Error" not in clean_text:
                                err_msg = clean_text[:200]

                final_msg = err_msg if err_msg else f"Falha na comunicação com o serviço de confirmação do Sankhya (HTTP {r.status_code})"
                return False, final_msg, None
        except Exception as e:
            print(f"[Sankhya] Exceção ao chamar CACSP.confirmarNota para #{nunota}: {e}")
            return False, str(e), None

    def _atualizar_campos_cabecalho(self, nunota, campos_dict, codusu=None):
        """
        Atualiza múltiplos campos de TGFCAB via CRUDServiceProvider.saveRecord na entidade CabecalhoNota.
        Ex: campos_dict = {"STATUSNOTA": "L", "PENDENTE": "N"}
        """
        if not self.jsessionid:
            if not self.autenticar():
                return False, "Não foi possível autenticar na API do Sankhya."

        try:
            nunota = int(nunota)
        except (ValueError, TypeError):
            return False, "NUNOTA inválido."

        local_fields = {str(k).upper(): {"$": str(v)} for k, v in campos_dict.items()}
        field_list = list(local_fields.keys())

        if codusu is not None:
            try:
                codusu_int = int(codusu)
                local_fields["CODUSU"] = {"$": str(codusu_int)}
                if "CODUSU" not in field_list:
                    field_list.append("CODUSU")
            except (ValueError, TypeError):
                pass

        url = f"{self.base_url}/mge/service.sbr?serviceName=CRUDServiceProvider.saveRecord&outputType=json&mgeSession={self.jsessionid}"
        payload = {
            "serviceName": "CRUDServiceProvider.saveRecord",
            "requestBody": {
                "dataSet": {
                    "rootEntity": "CabecalhoNota",
                    "dataRow": {
                        "key": {"NUNOTA": {"$": str(nunota)}},
                        "localFields": local_fields
                    },
                    "entity": {"fieldset": {"list": ",".join(field_list)}}
                }
            }
        }
        try:
            r = self.session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200 and r.json().get("status") == "1":
                print(f"[Sankhya] Pedido #{nunota} campos {campos_dict} atualizados com sucesso em TGFCAB!")
                return True, "Campos atualizados com sucesso!"
            else:
                msg = r.json().get("statusMessage", "Erro ao atualizar campos em TGFCAB.")
                print(f"[Sankhya] Erro ao atualizar TGFCAB #{nunota}: {msg}")
                return False, msg
        except Exception as e:
            print(f"[Sankhya] Exceção ao atualizar TGFCAB #{nunota}: {e}")
            return False, str(e)

    def gerar_pdf_pedido(self, nunota):
        """
        Gera e retorna os bytes do PDF do pedido/nota utilizando o serviço nativo de relatórios formatados do Sankhya OM (VisualizadorRelatorios.visualizarRelatorio).
        Consulta o modelo de relatório (NURFE) configurado na TOP da nota (TGFTOP -> TGFMON -> TSIRFE) ou utiliza o modelo padrão de pedido de venda.
        Retorna: (sucesso: bool, pdf_bytes: bytes, filename: str)
        """
        if not self.jsessionid:
            if not self.autenticar():
                return False, None, "Falha de autenticação no Sankhya."

        nunota = int(nunota)
        # 1. Identifica a TOP, o modelo NURFE e o STATUSNOTA da nota
        sql_top = f"""
        SELECT TOP.CODMODNF, MON.NURFE, TOP.DESCROPER, NVL(CAB.STATUSNOTA, 'P') AS STATUSNOTA
        FROM TGFCAB CAB
        LEFT JOIN TGFTOP TOP ON CAB.CODTIPOPER = TOP.CODTIPOPER AND CAB.DHTIPOPER = TOP.DHALTER
        LEFT JOIN TGFMON MON ON TOP.CODMODNF = MON.CODMODNF
        WHERE CAB.NUNOTA = {nunota}
        """
        res_top = self._executar_sql(sql_top)
        if not res_top or res_top.get("status") != "1" or not res_top.get("responseBody", {}).get("rows"):
            return False, None, f"Pedido #{nunota} não encontrado."

        row = res_top["responseBody"]["rows"][0]
        statusnota = str(row[3]).strip().upper() if len(row) > 3 and row[3] else "P"
        if statusnota != "L":
            return False, None, f"Impressão não permitida: O pedido #{nunota} precisa estar confirmado."

        nurfe = 59  # Modelo padrão PEDIDO_EXPEDICAO / Pedido de Venda
        if row[1]:
            nurfe = int(row[1])

        url_gen = f"{self.base_url}/mge/service.sbr?serviceName=VisualizadorRelatorios.visualizarRelatorio&outputType=json&mgeSession={self.jsessionid}"
        
        # Tentamos com o modelo da TOP (ex: 59) e com fallback para 4 se necessário
        modelos_tentativa = [nurfe] if nurfe != 4 else [4]
        if 4 not in modelos_tentativa:
            modelos_tentativa.append(4)
        if 59 not in modelos_tentativa:
            modelos_tentativa.append(59)

        for modelo in modelos_tentativa:
            payload = {
                "serviceName": "VisualizadorRelatorios.visualizarRelatorio",
                "requestBody": {
                    "relatorio": {
                        "nuRfe": str(modelo),
                        "parametros": {
                            "parametro": [
                                {
                                    "nome": "NUNOTA",
                                    "classe": "java.math.BigDecimal",
                                    "valor": str(nunota)
                                }
                            ]
                        }
                    }
                }
            }

            try:
                r_gen = self.session.post(url_gen, json=payload, timeout=20)
                json_data = r_gen.json()
                if json_data.get("status") == "1" and json_data.get("responseBody", {}).get("chave", {}).get("valor"):
                    chave = json_data["responseBody"]["chave"]["valor"]
                    url_pdf = f"{self.base_url}/mge/visualizadorArquivos.mge?hidemail=S&download=S&chaveArquivo={chave}&mgeSession={self.jsessionid}"
                    r_pdf = self.session.get(url_pdf, timeout=25)
                    if r_pdf.status_code == 200 and len(r_pdf.content) > 100 and r_pdf.content.startswith(b'%PDF'):
                        filename = f"Pedido_{nunota}.pdf"
                        print(f"[Sankhya] PDF do Pedido #{nunota} gerado com sucesso via modelo NURFE #{modelo} ({len(r_pdf.content)} bytes)!")
                        return True, r_pdf.content, filename
            except Exception as e:
                print(f"[Impressão Pedido] Erro ao gerar com modelo NURFE {modelo}: {e}")

    def obter_impressoras_rede(self):
        """
        Retorna a lista de impressoras ativas cadastradas no servidor de impressão Sankhya (TSIPRN + TSISVP).
        """
        if not self.jsessionid:
            self.autenticar()
        sql = """
        SELECT 
            PRN.NUPRINTER,
            PRN.NOME AS NOME_IMPRESSORA,
            NVL(PRN.ALIASLOCAL, PRN.NOME) AS ALIASLOCAL,
            PRN.PRINTERURI,
            PRN.STATUS,
            PRN.ATIVO,
            NVL(SVP.NUSVP, 1) AS NUSVP,
            NVL(SVP.DESCRICAO, 'Servidor de Impressão') AS NOME_SERVIDOR,
            NVL(SVP.URL, '') AS URL_SERVIDOR,
            NVL(SVP.PORTA, 19091) AS PORTA_SERVIDOR
        FROM TSIPRN PRN
        LEFT JOIN TSISVP SVP ON PRN.NUSVP = SVP.NUSVP
        WHERE PRN.ATIVO = 'S'
        ORDER BY PRN.NOME ASC
        """
        res = self._executar_sql(sql)
        impressoras = []
        if res and res.get("status") == "1" and res.get("responseBody", {}).get("rows"):
            for r in res["responseBody"]["rows"]:
                impressoras.append({
                    "nuprinter": r[0],
                    "nome": str(r[1]).strip() if r[1] else "",
                    "alias": str(r[2]).strip() if r[2] else "",
                    "uri": str(r[3]).strip() if r[3] else "",
                    "status": str(r[4]).strip() if r[4] else "Ativa",
                    "ativo": str(r[5]).strip() if r[5] else "S",
                    "nusvp": r[6],
                    "servidor": str(r[7]).strip() if r[7] else "Servidor Padrão",
                    "url_servidor": str(r[8]).strip() if r[8] else "",
                    "porta_servidor": r[9]
                })
        return impressoras

    def imprimir_pedido_servidor(self, nunota, impressora_nome, nusvp=1, copias=1):
        """
        Envia a ordem de impressão do pedido diretamente para a impressora de rede através do Servidor de Impressão do Sankhya.
        """
        if not self.jsessionid:
            if not self.autenticar():
                return False, "Falha de autenticação no Sankhya."

        nunota = int(nunota)
        # 1. Identifica a TOP, o modelo NURFE e o STATUSNOTA da nota
        sql_top = f"""
        SELECT TOP.CODMODNF, MON.NURFE, TOP.DESCROPER, NVL(CAB.STATUSNOTA, 'P') AS STATUSNOTA
        FROM TGFCAB CAB
        LEFT JOIN TGFTOP TOP ON CAB.CODTIPOPER = TOP.CODTIPOPER AND CAB.DHTIPOPER = TOP.DHALTER
        LEFT JOIN TGFMON MON ON TOP.CODMODNF = MON.CODMODNF
        WHERE CAB.NUNOTA = {nunota}
        """
        res_top = self._executar_sql(sql_top)
        if not res_top or res_top.get("status") != "1" or not res_top.get("responseBody", {}).get("rows"):
            return False, f"Pedido #{nunota} não encontrado."

        row = res_top["responseBody"]["rows"][0]
        statusnota = str(row[3]).strip().upper() if len(row) > 3 and row[3] else "P"
        if statusnota != "L":
            return False, f"Impressão não permitida: O pedido #{nunota} precisa estar confirmado."

        nurfe = 59  # Modelo padrão PEDIDO_EXPEDICAO / Pedido de Venda
        if row[1]:
            nurfe = int(row[1])

        url_gen = f"{self.base_url}/mge/service.sbr?serviceName=VisualizadorRelatorios.visualizarRelatorio&outputType=json&mgeSession={self.jsessionid}"
        
        modelos_tentativa = [nurfe] if nurfe != 4 else [4]
        if 4 not in modelos_tentativa:
            modelos_tentativa.append(4)
        if 59 not in modelos_tentativa:
            modelos_tentativa.append(59)

        for modelo in modelos_tentativa:
            payload = {
                "serviceName": "VisualizadorRelatorios.visualizarRelatorio",
                "requestBody": {
                    "relatorio": {
                        "nuRfe": str(modelo),
                        "servidorImpressao": str(nusvp or 1),
                        "impressora": str(impressora_nome),
                        "copias": int(copias or 1),
                        "parametros": {
                            "parametro": [
                                {
                                    "nome": "NUNOTA",
                                    "classe": "java.math.BigDecimal",
                                    "valor": str(nunota)
                                }
                            ]
                        }
                    }
                }
            }

            try:
                r_gen = self.session.post(url_gen, json=payload, timeout=20)
                json_data = r_gen.json()
                if json_data.get("status") == "1":
                    print(f"[Sankhya] Pedido #{nunota} enviado com sucesso para a impressora '{impressora_nome}' (Servidor #{nusvp}, Modelo #{modelo})!")
                    return True, f"Impressão do pedido #{nunota} enviada com sucesso para a impressora '{impressora_nome}'!"
                elif json_data.get("statusMessage"):
                    msg_err = json_data.get("statusMessage")
                    # Se houver conflito de PK em AD_TGFCNT (mesmo segundo), aguarda e tenta novamente
                    if "PK_AD_TGFCNT" in msg_err:
                        time.sleep(1.1)
                        r_retry = self.session.post(url_gen, json=payload, timeout=20)
                        if r_retry.json().get("status") == "1":
                            return True, f"Impressão do pedido #{nunota} enviada com sucesso para a impressora '{impressora_nome}'!"
                    print(f"[Impressão Servidor] Erro com modelo {modelo}: {msg_err}")
            except Exception as e:
                print(f"[Impressão Servidor] Exceção com modelo {modelo}: {e}")

        return False, "Não foi possível enviar a impressão para a impressora selecionada no servidor Sankhya."

    def obter_pix_copiacola(self, nunota):
        """
        Executa a validação e consulta do PIX Copia e Cola para o pedido (nunota),
        reproduzindo fielmente a ação da procedure STP_BA_EXIBECOPIACOLAPIX_AG no Sankhya.
        """
        if not nunota or nunota <= 0:
            return {
                "sucesso": False,
                "codigo_erro": "PEDIDO_INVALIDO",
                "titulo": "Aviso",
                "mensagem": "O PIX Copia e Cola não foi gerado.\nClique em fechar pedido.",
                "detalhes": ""
            }

        sql = f"""
        SELECT 
            CAB.CODTIPVENDA,
            CAB.STATUSNOTA,
            CAB.PENDENTE,
            CAB.AD_PIX_LINK
        FROM 
            TGFCAB CAB
        WHERE 
            CAB.NUNOTA = {nunota}
        """
        res = self._executar_sql(sql)
        if not res or res.get("status") != "1" or not res.get("responseBody", {}).get("rows"):
            return {
                "sucesso": False,
                "codigo_erro": "PEDIDO_NAO_ENCONTRADO",
                "titulo": "Aviso",
                "mensagem": "Pedido não encontrado no Sankhya.",
                "detalhes": ""
            }

        row = res["responseBody"]["rows"][0]
        codtipvenda = int(row[0]) if row[0] is not None else 0
        statusnota = str(row[1]).strip() if row[1] else ""
        pendente = str(row[2]).strip() if row[2] else "S"
        ad_pix_link = str(row[3]).strip() if len(row) > 3 and row[3] is not None else ""

        # Regra 1: Tipo de Negociação precisa ser 76
        if codtipvenda != 76:
            return {
                "sucesso": False,
                "codigo_erro": "NEGOCIACAO_INVALIDA",
                "titulo": "Aviso",
                "mensagem": "A forma de pagamento precisa ser 76 - VENDA PIX / TRANSFERENCIA.",
                "detalhes": ""
            }

        # Regra 2: Pedido precisa estar fechado e possuir AD_PIX_LINK gerado
        if not ad_pix_link or statusnota != "L":
            return {
                "sucesso": False,
                "codigo_erro": "PIX_NAO_GERADO",
                "titulo": "Aviso",
                "mensagem": "O PIX Copia e Cola não foi gerado.\nClique em fechar pedido.",
                "detalhes": ""
            }

        # Sucesso: Retorna o link PIX Copia e Cola
        return {
            "sucesso": True,
            "titulo": "Informação",
            "pix_copia_cola": ad_pix_link,
            "mensagem": "PIX Copia e Cola:"
        }

    def consultar_faturamento_geral(self, dt_ini=None, dt_fim=None, codemp=None, codvend=None):
        """
        Executa a consulta de Faturamento Geral na view/tabela CND_FAT_GERAL
        com filtros opcionais de período (dt_ini, dt_fim), empresa e vendedor.
        """
        filtros_sql = ["1 = 1"]

        if dt_ini:
            filtros_sql.append(f"CASE WHEN FG.NFE = 'N' THEN FG.DTNEG ELSE FG.DTENTSAI END >= TO_DATE('{dt_ini}', 'YYYY-MM-DD')")
        else:
            filtros_sql.append("CASE WHEN FG.NFE = 'N' THEN FG.DTNEG ELSE FG.DTENTSAI END >= TRUNC(SYSDATE, 'MM')")

        if dt_fim:
            filtros_sql.append(f"CASE WHEN FG.NFE = 'N' THEN FG.DTNEG ELSE FG.DTENTSAI END <= TO_DATE('{dt_fim}', 'YYYY-MM-DD')")
        else:
            filtros_sql.append("CASE WHEN FG.NFE = 'N' THEN FG.DTNEG ELSE FG.DTENTSAI END <= TRUNC(SYSDATE)")

        if codemp:
            if isinstance(codemp, (list, tuple)):
                emp_str = ",".join(str(int(e)) for e in codemp)
                filtros_sql.append(f"FG.CODEMP IN ({emp_str})")
            else:
                filtros_sql.append(f"FG.CODEMP = {int(codemp)}")

        if codvend:
            if isinstance(codvend, (list, tuple)):
                vend_str = ",".join(str(int(v)) for v in codvend)
                filtros_sql.append(f"FG.CODVEND IN ({vend_str})")
            else:
                filtros_sql.append(f"FG.CODVEND = {int(codvend)}")

        where_clause = " AND ".join(filtros_sql)

        sql = f"""
        SELECT
            FG.NOMEFANTASIA,
            FG.CODVEND,
            FG.APELIDO,
            NVL(SUM(FG.TOTAL),0) AS TOTAL,
            NVL(SUM(CASE WHEN FG.NUPROMOCAO IS NOT NULL THEN TOTAL END),0) AS VENDAPROMOCAO,
            NVL(SUM(CASE WHEN FG.NUPROMOCAO IS NULL THEN TOTAL END),0) AS VENDASEMPROMOCAO,
            SUM(FG.QTDNEG * FG.GOLDEV) AS QTDNEG,
            ROUND(RATIO_TO_REPORT(NVL(SUM(FG.TOTAL),0))OVER() * 100, 4) AS PERCVAL,
            ROUND(RATIO_TO_REPORT(NVL(SUM(FG.QTDNEG),0))OVER() * 100, 4) AS PERCQTD
        FROM
            CND_FAT_GERAL FG
        WHERE {where_clause}
        GROUP BY
             FG.NOMEFANTASIA,
             FG.CODVEND,
             FG.APELIDO
        ORDER BY 
            TOTAL DESC
        """

        dados = self._executar_sql(sql)
        linhas = []
        totais = {
            "total": 0.0,
            "perc_val": 100.0,
            "qtd_neg": 0.0,
            "perc_qtd": 100.0,
            "venda_promocao": 0.0,
            "venda_sem_promocao": 0.0,
            "perc_sem_promocao": 0.0,
            "perc_promocao": 0.0
        }

        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                nomefantasia = str(r[0]) if r[0] else ""
                codvend_val = int(r[1]) if r[1] is not None else 0
                apelido = str(r[2]) if r[2] else ""
                total = float(r[3] or 0.0)
                venda_promocao = float(r[4] or 0.0)
                venda_sem_promocao = float(r[5] or 0.0)
                qtd_neg = float(r[6] or 0.0)
                perc_val = float(r[7] or 0.0)
                perc_qtd = float(r[8] or 0.0)

                perc_sem_promocao = (venda_sem_promocao / total * 100.0) if total > 0 else 0.0
                perc_com_promocao = (venda_promocao / total * 100.0) if total > 0 else 0.0

                totais["total"] += total
                totais["venda_promocao"] += venda_promocao
                totais["venda_sem_promocao"] += venda_sem_promocao
                totais["qtd_neg"] += qtd_neg

                linhas.append({
                    "nomefantasia": nomefantasia,
                    "codvend": codvend_val,
                    "apelido": apelido,
                    "total": total,
                    "venda_promocao": venda_promocao,
                    "venda_sem_promocao": venda_sem_promocao,
                    "qtd_neg": qtd_neg,
                    "perc_val": perc_val,
                    "perc_qtd": perc_qtd,
                    "perc_sem_promocao": perc_sem_promocao,
                    "perc_promocao": perc_com_promocao
                })

            if totais["total"] > 0:
                totais["perc_sem_promocao"] = (totais["venda_sem_promocao"] / totais["total"]) * 100.0
                totais["perc_promocao"] = (totais["venda_promocao"] / totais["total"]) * 100.0

        return {"linhas": linhas, "totais": totais}

    def consultar_andamento_comissoes(self, dt_ini=None, dt_fim=None, codemp=None, codvend=None):
        """
        Executa a query de Andamento das Comissões por Vendedor / Faixas de Meta.
        Parametros:
          dt_ini: Data Inicial (YYYY-MM-DD)
          dt_fim: Data Final (YYYY-MM-DD)
          codemp: Código ou lista de empresas
          codvend: Código do vendedor obrigatório
        """
        # Formatação das datas para Oracle SQL
        if dt_ini:
            p_ini_expr = f"TO_DATE('{dt_ini}', 'YYYY-MM-DD')"
            p_ini_str = f"TO_CHAR(TO_DATE('{dt_ini}', 'YYYY-MM-DD'), 'DD/MM/YYYY')"
        else:
            p_ini_expr = "TRUNC(SYSDATE, 'MM')"
            p_ini_str = "TO_CHAR(TRUNC(SYSDATE, 'MM'), 'DD/MM/YYYY')"

        if dt_fim:
            p_fim_expr = f"TO_DATE('{dt_fim}', 'YYYY-MM-DD')"
            p_fim_str = f"TO_CHAR(TO_DATE('{dt_fim}', 'YYYY-MM-DD'), 'DD/MM/YYYY')"
        else:
            p_fim_expr = "TRUNC(SYSDATE)"
            p_fim_str = "TO_CHAR(TRUNC(SYSDATE), 'DD/MM/YYYY')"

        # Filtros de empresa
        vendas_codemp_filter = ""
        meta_codemp_filter = ""
        if codemp:
            if isinstance(codemp, (list, tuple)):
                emp_str = ",".join(str(int(e)) for e in codemp)
                vendas_codemp_filter = f"AND FG.CODEMP IN ({emp_str})"
                meta_codemp_filter = f"AND MET.CODEMP IN ({emp_str})"
            else:
                vendas_codemp_filter = f"AND FG.CODEMP = {int(codemp)}"
                meta_codemp_filter = f"AND MET.CODEMP = {int(codemp)}"

        # Vendedor
        v_codvend = int(codvend) if codvend else 0
        vendas_codvend_filter = f"AND FG.CODVEND = {v_codvend}" if v_codvend > 0 else ""
        meta_codvend_filter = f"AND MET.CODVEND = {v_codvend}" if v_codvend > 0 else ""

        sql = f"""
        WITH VENDAS AS (
            SELECT
                FG.CODVEND,
                NVL(SUM(FG.TOTAL), 0) AS TOTAL
            FROM
                CND_FAT_GERAL FG
            WHERE 1 = 1
                AND CASE WHEN FG.NFE = 'N' THEN FG.DTNEG ELSE FG.DTENTSAI END >= {p_ini_expr}
                AND CASE WHEN FG.NFE = 'N' THEN FG.DTNEG ELSE FG.DTENTSAI END <= {p_fim_expr}
                {vendas_codemp_filter}
                {vendas_codvend_filter}
            GROUP BY
                FG.CODVEND
        ),
        META AS (
            SELECT
                MET.REFERENCIA AS DTREF,
                MET.CODEMP,
                MET.CODVEND,
                VEN.APELIDO,
                MET.META AS PREVREC,
                DU.QTDDIAS,
                ROUND(MET.META / NULLIF(DU.QTDDIAS, 0), 2) AS MEDIAIDEAL,
                DUA.QTDDIAS AS QTDDIAS_PASSADOS_MES_ATUAL,
                (DU.QTDDIAS - CASE WHEN TO_CHAR(MET.REFERENCIA, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN NVL(DUA.QTDDIAS, 0) ELSE 0 END) AS DIAS_UTEIS_RESTANTES,
                FCT_METAMARCA_AG(MET.CODEMP, MET.CODVEND, MET.REFERENCIA) AS PERCMARCA
            FROM
                AD_TGFMTVRMV MET
                INNER JOIN TGFVEN VEN ON MET.CODVEND = VEN.CODVEND
                INNER JOIN (SELECT ANO, MES, QTDDIAS FROM VW_QTDDIAS_UTEIS) DU 
                    ON DU.ANO = EXTRACT(YEAR FROM {p_ini_expr}) AND DU.MES = TO_CHAR(MET.REFERENCIA, 'MM')
                LEFT JOIN (
                    SELECT 
                        COUNT(DATAS) AS QTDDIAS
                    FROM (    
                        SELECT 
                            TRUNC(SYSDATE, 'MM') + (LEVEL) AS DATAS
                        FROM 
                            DUAL
                        CONNECT BY 1 = 1
                            AND TRUNC(TO_DATE({p_ini_str}, 'DD/MM/YYYY'), 'MM') + (LEVEL - 1) <= TRUNC(TO_DATE({p_fim_str}, 'DD/MM/YYYY'))
                    )
                    WHERE 1 = 1
                        AND TO_CHAR(DATAS, 'd') NOT IN (1)
                        AND NOT EXISTS (
                            SELECT 1
                            FROM DTFERIADO_REC_ATUAL
                            WHERE DTFERIADO_ATUAL = DATAS
                        )
                    GROUP BY 
                        TO_CHAR(DATAS, 'MM'), 
                        TO_CHAR(DATAS, 'YYYY')
                ) DUA ON 1 = 1
            WHERE 1 = 1
                {meta_codemp_filter}
                {meta_codvend_filter}
                AND MET.REFERENCIA = {p_ini_expr}
        ),
        PERC AS (
            SELECT
                M.ATE + 0.01 AS ATE,
                M.PERC
            FROM
                AD_TGFMEC M
            ORDER BY
                PERC
        ),
        PERC2 AS (
            SELECT
                NVL(LAG(MEC.ATE) OVER (ORDER BY MEC.ATE), 0) AS ANTES,
                MEC.ATE AS ATE2,
                NVL(LEAD(MEC.ATE) OVER (ORDER BY MEC.ATE), MEC.ATE) AS DEPOIS,
                MEC.PERC AS PERC2,
                NVL(LEAD(MEC.PERC) OVER (ORDER BY MEC.PERC), MEC.PERC) AS PERCDEP
            FROM
                AD_TGFMEC MEC
        )
        SELECT DISTINCT
            ATE,
            ((ATE / 100) * PREVREC) AS FAIXA,
            LEAD(((ATE / 100) * PREVREC)) OVER (ORDER BY ATE) AS PROX,
            PERC,
            PREVREC,
            ALCANCADO,
            P2.PERC2,
            ROUND(((ATE / 100) * PREVREC), 2) AS PROXFAIXA,
            CASE WHEN ROUND((((ATE + 0.01) / 100) * PREVREC), 2) - ALCANCADO < 0 THEN 0 ELSE ROUND((((ATE + 0.01) / 100) * PREVREC), 2) - ALCANCADO END AS DIFPROXFAIXA,
            CASE WHEN ROUND((((ATE + 0.01) / 100) * PREVREC), 2) - ALCANCADO < 0 THEN 0 ELSE (PERC / 100) * ALCANCADO END AS COMISSAO,
            ROUND(CASE WHEN ROUND((((ATE + 0.01) / 100) * PREVREC), 2) - ALCANCADO < 0 THEN 0 ELSE ROUND(((ATE / 100) * PREVREC), 2) / NULLIF(QTDDIAS, 0) END, 2) AS MEDIADIAIDEAL,
            CASE 
                WHEN PERC = P2.PERC2 THEN 'PALEGREEN' 
                WHEN PERC < P2.PERC2 THEN 'TOMATO'
                ELSE NULL
            END AS BKCOLOR,
            APELIDO
        FROM (
            SELECT DISTINCT
                MET.DTREF,
                MET.CODVEND,
                MET.APELIDO,
                PER.ATE,
                PER.PERC,
                SUM(MET.PREVREC) AS PREVREC,
                SUM(VEN.TOTAL) AS ALCANCADO,
                (CASE WHEN SUM(MET.PREVREC) = 0 THEN 0 ELSE SUM(VEN.TOTAL) END / CASE WHEN SUM(MET.PREVREC) = 0 THEN 1 ELSE SUM(MET.PREVREC) END) * 100 AS PERCREAL,
                ROUND(SUM(VEN.TOTAL) / NULLIF(CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN MET.QTDDIAS_PASSADOS_MES_ATUAL ELSE MET.QTDDIAS END, 0), 2) AS MEDIADIA,
                CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN MET.QTDDIAS_PASSADOS_MES_ATUAL ELSE MET.QTDDIAS END AS DIASUTEISFALTAM,
                MET.QTDDIAS,
                CASE WHEN SUM(MET.PREVREC) = 0 THEN 0 ELSE SUM(MET.PREVREC) - SUM(VEN.TOTAL) END AS DIF,
                SUM(ROUND(MET.PREVREC / NULLIF(MET.QTDDIAS, 0), 2)) AS MEDIAIDEAL,
                CASE WHEN SUM(MET.PREVREC) = 0 THEN 0
                ELSE
                    ((ROUND(SUM(VEN.TOTAL) / NULLIF(CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN MET.QTDDIAS_PASSADOS_MES_ATUAL ELSE MET.QTDDIAS END, 0), 2) /
                    NULLIF(CASE WHEN (SUM(MET.PREVREC) / NULLIF(MET.QTDDIAS, 0)) = 0 THEN 1 ELSE (SUM(MET.PREVREC) / NULLIF(MET.QTDDIAS, 0)) END, 0)) * 100) - 100
                END AS PERCDIA,
                CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN SUM(VEN.TOTAL) ELSE 0 END + 
                ROUND(SUM(VEN.TOTAL) / NULLIF(CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN MET.QTDDIAS_PASSADOS_MES_ATUAL ELSE MET.QTDDIAS END, 0), 2) * 
                (MET.QTDDIAS - CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN NVL(MET.QTDDIAS_PASSADOS_MES_ATUAL, 0) ELSE 0 END) AS PROJECAO,
                CASE WHEN SUM(MET.PREVREC) = 0 THEN 0
                ELSE
                    (((CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN SUM(VEN.TOTAL) ELSE 0 END + 
                    ROUND(SUM(VEN.TOTAL) / NULLIF(CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN MET.QTDDIAS_PASSADOS_MES_ATUAL ELSE MET.QTDDIAS END, 0), 2) * 
                    (MET.QTDDIAS - CASE WHEN TO_CHAR(MET.DTREF, 'MM/YYYY') = TO_CHAR(SYSDATE, 'MM/YYYY') THEN NVL(MET.QTDDIAS_PASSADOS_MES_ATUAL, 0) ELSE 0 END)) / NULLIF(CASE WHEN SUM(MET.PREVREC) = 0 THEN 1 ELSE SUM(MET.PREVREC) END, 0)) * 100)
                END AS PERCPROJ
            FROM
                VENDAS VEN
                LEFT JOIN META MET ON VEN.CODVEND = MET.CODVEND
                INNER JOIN PERC PER ON 1 = 1
            GROUP BY
                MET.DTREF,
                MET.CODVEND,
                MET.APELIDO,
                MET.QTDDIAS_PASSADOS_MES_ATUAL,
                MET.QTDDIAS,
                MET.PERCMARCA,
                PER.ATE,
                PER.PERC
            ORDER BY
                PER.ATE
        ) X,
        PERC2 P2
        WHERE
            X.PERCREAL BETWEEN P2.ANTES AND P2.ATE2
        ORDER BY
            ATE
        """

        dados = self._executar_sql(sql)
        faixas = []
        info_vendedor = {
            "codvend": v_codvend,
            "apelido": "",
            "prevrec": 0.0,
            "alcancado": 0.0,
            "percreal": 0.0
        }

        if dados and dados.get("status") == "1":
            rows = dados.get("responseBody", {}).get("rows", [])
            for r in rows:
                ate = float(r[0] or 0.0)
                faixa = float(r[1] or 0.0)
                prox = float(r[2] or 0.0) if r[2] is not None else 0.0
                perc = float(r[3] or 0.0)
                prevrec = float(r[4] or 0.0)
                alcancado = float(r[5] or 0.0)
                perc2 = float(r[6] or 0.0)
                proxfaixa = float(r[7] or 0.0)
                difproxfaixa = float(r[8] or 0.0)
                comissao = float(r[9] or 0.0)
                mediadiaideal = float(r[10] or 0.0)
                bkcolor = str(r[11] or "") if r[11] else ""
                apelido = str(r[12] or "") if len(r) > 12 and r[12] else ""

                if apelido:
                    info_vendedor["apelido"] = apelido
                if prevrec:
                    info_vendedor["prevrec"] = prevrec
                if alcancado:
                    info_vendedor["alcancado"] = alcancado
                    if prevrec > 0:
                        info_vendedor["percreal"] = (alcancado / prevrec) * 100.0

                faixas.append({
                    "ate": ate,
                    "faixa": faixa,
                    "prox": prox,
                    "perc": perc,
                    "prevrec": prevrec,
                    "alcancado": alcancado,
                    "perc2": perc2,
                    "proxfaixa": proxfaixa,
                    "difproxfaixa": difproxfaixa,
                    "comissao": comissao,
                    "mediadiaideal": mediadiaideal,
                    "bkcolor": bkcolor,
                    "ativo": (bkcolor.upper() == "PALEGREEN")
                })

        return {"faixas": faixas, "info": info_vendedor}

    def obter_opcoes_filtros_promocoes(self):
        """
        Retorna as opções disponíveis para os filtros multlist de promoções:
        - Empresas (CODEMP, NOME)
        - Marcas distintas ativas
        - Grupos de produto (CODGRUPOPROD, DESCRGRUPOPROD)
        """
        sql_emp = """
        SELECT DISTINCT DES.CODEMP, NVL(EMP.NOMEFANTASIA, EMP.RAZAOSOCIAL) AS NOMEEMP
        FROM TGFDES DES
        LEFT JOIN TSIEMP EMP ON DES.CODEMP = EMP.CODEMP
        WHERE TRUNC(DES.DTINICIAL) <= TRUNC(SYSDATE)
          AND TRUNC(DES.DTFINAL) >= TRUNC(SYSDATE)
        ORDER BY DES.CODEMP
        """
        sql_marcas = """
        SELECT DISTINCT PRO.MARCA
        FROM TGFDES DES
        INNER JOIN TGFPRO PRO ON DES.CODPROD = PRO.CODPROD
        WHERE PRO.ATIVO = 'S' AND PRO.USOPROD = 'R'
          AND TRUNC(DES.DTINICIAL) <= TRUNC(SYSDATE)
          AND TRUNC(DES.DTFINAL) >= TRUNC(SYSDATE)
          AND PRO.MARCA IS NOT NULL
        ORDER BY PRO.MARCA
        """
        sql_grupos = """
        SELECT DISTINCT PRO.CODGRUPOPROD, GRU.DESCRGRUPOPROD
        FROM TGFDES DES
        INNER JOIN TGFPRO PRO ON DES.CODPROD = PRO.CODPROD
        LEFT JOIN TGFGRU GRU ON PRO.CODGRUPOPROD = GRU.CODGRUPOPROD
        WHERE PRO.ATIVO = 'S' AND PRO.USOPROD = 'R'
          AND TRUNC(DES.DTINICIAL) <= TRUNC(SYSDATE)
          AND TRUNC(DES.DTFINAL) >= TRUNC(SYSDATE)
        ORDER BY GRU.DESCRGRUPOPROD
        """

        empresas = [{"codemp": 0, "nome": "Todas as Empresas (Geral)"}]
        marcas = []
        grupos = []

        try:
            res_e = self._executar_sql(sql_emp)
            for r in res_e.get("responseBody", {}).get("rows", []):
                cod = int(r[0]) if r[0] is not None else 0
                nome = r[1] or (f"Empresa {cod}" if cod > 0 else "Todas as Empresas (Geral)")
                if not any(e["codemp"] == cod for e in empresas):
                    empresas.append({"codemp": cod, "nome": nome})
        except Exception as e:
            logger.warning(f"Erro ao obter empresas de promoção: {e}")

        try:
            res_m = self._executar_sql(sql_marcas)
            for r in res_m.get("responseBody", {}).get("rows", []):
                if r[0]:
                    marcas.append(str(r[0]).strip())
        except Exception as e:
            logger.warning(f"Erro ao obter marcas de promoção: {e}")

        try:
            res_g = self._executar_sql(sql_grupos)
            for r in res_g.get("responseBody", {}).get("rows", []):
                if r[0] is not None:
                    grupos.append({
                        "codgrupo": int(r[0]),
                        "codgrupoprod": int(r[0]),
                        "descr": r[1] or f"Grupo {r[0]}",
                        "descrgrupoprod": r[1] or f"Grupo {r[0]}"
                    })
        except Exception as e:
            logger.warning(f"Erro ao obter grupos de promoção: {e}")

        return {
            "empresas": empresas,
            "marcas": marcas,
            "grupos": grupos
        }

    def consultar_promocoes_ativas(self, filtros=None):
        """
        Executa a query de promoções ativas com filtros multlist flexíveis:
        - data_ini / data_fin (Vigência)
        - empresas (lista de CODEMP)
        - marcas (lista de MARCA)
        - grupos (lista de CODGRUPOPROD)
        - busca (Código, Referência do Fornecedor ou Descrição do Produto)
        """
        if filtros is None:
            filtros = {}

        codemp_loja = int(filtros.get("codemp_loja") or 1)

        where_clauses = [
            "PRO.ATIVO = 'S'",
            "PRO.USOPROD = 'R'"
        ]

        # Vigência
        data_ini = filtros.get("data_ini")
        data_fin = filtros.get("data_fin")
        if data_ini:
            where_clauses.append(f"TRUNC(DES.DTINICIAL) <= TO_DATE('{data_ini}', 'DD/MM/YYYY')")
        else:
            where_clauses.append("TRUNC(DES.DTINICIAL) <= TRUNC(SYSDATE)")

        if data_fin:
            where_clauses.append(f"TRUNC(DES.DTFINAL) >= TO_DATE('{data_fin}', 'DD/MM/YYYY')")
        else:
            where_clauses.append("TRUNC(DES.DTFINAL) >= TRUNC(SYSDATE)")

        # Empresas (multlist)
        empresas = filtros.get("empresas", [])
        if empresas:
            emp_ids = [str(int(e)) for e in empresas if str(e).isdigit() and int(e) > 0]
            if emp_ids:
                emp_str = ",".join(emp_ids)
                where_clauses.append(f"(DES.CODEMP IN ({emp_str}) OR DES.CODEMP = 0)")

        # Marcas (multlist)
        marcas = filtros.get("marcas", [])
        if marcas:
            marcas_san = [m.replace("'", "''") for m in marcas if m]
            if marcas_san:
                marcas_str = ",".join(f"'{m}'" for m in marcas_san)
                where_clauses.append(f"PRO.MARCA IN ({marcas_str})")

        # Grupos de Produto (multlist)
        grupos = filtros.get("grupos", [])
        if grupos:
            grupos_ids = [str(int(g)) for g in grupos if str(g).isdigit() and int(g) > 0]
            if grupos_ids:
                grupos_str = ",".join(grupos_ids)
                where_clauses.append(f"PRO.CODGRUPOPROD IN ({grupos_str})")

        # Busca por Código, Referência do Fornecedor ou Descrição
        busca = filtros.get("busca", "").strip().upper().replace("'", "''")
        if busca:
            if busca.isdigit():
                where_clauses.append(f"(PRO.CODPROD = {busca} OR UPPER(PRO.REFFORN) LIKE '%{busca}%' OR UPPER(PRO.DESCRPROD) LIKE '%{busca}%')")
            else:
                where_clauses.append(f"(UPPER(PRO.REFFORN) LIKE '%{busca}%' OR UPPER(PRO.DESCRPROD) LIKE '%{busca}%' OR UPPER(PRO.COMPLDESC) LIKE '%{busca}%')")

        where_sql = " AND ".join(where_clauses)
        sql = f"""
        SELECT
            DES.CODEMP,
            PRO.CODGRUPOPROD,
            GRU.DESCRGRUPOPROD,
            DES.CODPROD,
            PRO.REFFORN AS REF_FORN,
            PRO.MARCA,
            PRO.DESCRPROD,
            PRO.COMPLDESC,
            PRO.CODVOL,
            NVL(SNK_PRECO(0, DES.CODPROD), 0) AS PRECO_NORMAL,
            ABS(NVL(SNK_PRECO(0, DES.CODPROD), 0) - (CASE
                                                 WHEN NVL(DES.PERCENTUAL,0) > 0
                                                 THEN  NVL(SNK_PRECO(0, DES.CODPROD), 0) *  (DES.PERCENTUAL/100)
                                                WHEN NVL(DES.VLRDESC,0) > 0
                                                THEN  DES.VLRDESC
                                                WHEN NVL(DES.VLRVENDA,0) > 0
                                                THEN (NVL(SNK_PRECO(0, DES.CODPROD), 0) + DES.VLRVENDA)
                                                ELSE 0
                                             END)) AS PRECO_CDESCONTO,
            'Sim' AS PROMOCAO,
            TO_CHAR(DES.DTINICIAL, 'DD/MM/YYYY') AS DT_VIGORINI,
            TO_CHAR(DES.DTFINAL, 'DD/MM/YYYY') AS DT_VIGORFIN,
            NVL((SELECT NVL(SUM(EST.ESTOQUE - EST.RESERVADO), 0) FROM TGFEST EST WHERE EST.CODPROD = DES.CODPROD AND EST.CODEMP = {codemp_loja} AND EST.CODLOCAL = 1000000), 0) AS ESTOQUE
        FROM
            TGFDES DES
            INNER JOIN TGFPRO PRO ON DES.CODPROD = PRO.CODPROD
            LEFT JOIN TGFGRU GRU ON PRO.CODGRUPOPROD = GRU.CODGRUPOPROD
            LEFT JOIN TGFPAR FORN ON (FORN.CODPARC = PRO.CODPARCFORN)
        WHERE
            {where_sql}
        ORDER BY PRO.DESCRPROD ASC
        """

        res = self._executar_sql(sql)
        raw_rows = res.get("responseBody", {}).get("rows", [])

        lista_produtos = []
        marcas_encontradas = set()
        grupos_encontrados = set()
        maior_desconto = 0.0
        soma_descontos = 0.0

        for r in raw_rows:
            codemp = int(r[0]) if r[0] is not None else 0
            codgrupo = int(r[1]) if r[1] is not None else 0
            descrgrupo = r[2] or "DIVERSOS"
            codprod = int(r[3]) if r[3] is not None else 0
            ref_forn = r[4] or ""
            marca = r[5] or "SEM MARCA"
            descrprod = r[6] or ""
            compldesc = r[7] or ""
            codvol = r[8] or "UN"
            preco_normal = round(float(r[9] or 0.0), 2)
            preco_cdesconto = round(float(r[10] or 0.0), 2)
            promocao = r[11] or "Sim"
            dt_vigorini = r[12] or ""
            dt_vigorfin = r[13] or ""
            estoque = int(r[14]) if len(r) > 14 and r[14] is not None else 0

            desconto_valor = round(max(0.0, preco_normal - preco_cdesconto), 2)
            desconto_perc = round((desconto_valor / preco_normal * 100), 1) if preco_normal > 0 else 0.0

            if desconto_perc > maior_desconto:
                maior_desconto = desconto_perc
            soma_descontos += desconto_perc
            marcas_encontradas.add(marca)
            grupos_encontrados.add(descrgrupo)

            lista_produtos.append({
                "codemp": codemp,
                "codgrupoprod": codgrupo,
                "descrgrupoprod": descrgrupo,
                "codprod": codprod,
                "ref_forn": ref_forn,
                "marca": marca,
                "descrprod": descrprod,
                "compldesc": compldesc,
                "codvol": codvol,
                "preco_normal": preco_normal,
                "preco_cdesconto": preco_cdesconto,
                "desconto_valor": desconto_valor,
                "desconto_perc": desconto_perc,
                "promocao": promocao,
                "dt_vigorini": dt_vigorini,
                "dt_vigorfin": dt_vigorfin,
                "estoque": estoque
            })

        total = len(lista_produtos)
        desconto_medio = round(soma_descontos / total, 1) if total > 0 else 0.0

        return {
            "produtos": lista_produtos,
            "total": total,
            "estatisticas": {
                "total_produtos": total,
                "total_marcas": len(marcas_encontradas),
                "total_grupos": len(grupos_encontrados),
                "maior_desconto_perc": maior_desconto,
                "desconto_medio_perc": desconto_medio
            }
        }













