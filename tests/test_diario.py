import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "artia"))
import diario


def tarefa(nome):
    return {"nome": nome, "peso": 1}


def horarios(blocos):
    return [(b["tarefa"]["nome"], b["inicio"], b["fim"]) for b in blocos]


class DividirHorarios(unittest.TestCase):
    def test_uma_tarefa_ocupa_manha_e_tarde_sem_cruzar_o_almoco(self):
        self.assertEqual(horarios(diario.dividir_horarios([tarefa("A")])),
                         [("A", "08:00", "12:00"), ("A", "13:30", "18:00")])

    def test_duas_tarefas_uma_de_manha_outra_a_tarde(self):
        self.assertEqual(horarios(diario.dividir_horarios([tarefa("A"), tarefa("B")])),
                         [("A", "08:00", "12:00"), ("B", "13:30", "18:00")])

    def test_tres_tarefas_a_tarde_mais_longa_recebe_duas(self):
        self.assertEqual(horarios(diario.dividir_horarios([tarefa("A"), tarefa("B"), tarefa("C")])),
                         [("A", "08:00", "12:00"), ("B", "13:30", "16:00"), ("C", "16:00", "18:00")])

    def test_quatro_tarefas_duas_por_periodo(self):
        self.assertEqual(horarios(diario.dividir_horarios([tarefa(x) for x in "ABCD"])),
                         [("A", "08:00", "10:00"), ("B", "10:00", "12:00"),
                          ("C", "13:30", "16:00"), ("D", "16:00", "18:00")])

    def test_blocos_nunca_se_sobrepoem_e_somam_oito_horas_e_meia(self):
        for n in range(1, 12):
            blocos = diario.dividir_horarios([tarefa(str(i)) for i in range(n)])
            self.assertAlmostEqual(sum(b["horas"] for b in blocos), 8.5)
            intervalos = sorted((b["inicio"], b["fim"]) for b in blocos)
            for (_, fim), (inicio, _) in zip(intervalos, intervalos[1:]):
                self.assertLessEqual(fim, inicio)
            for b in blocos:
                self.assertFalse(b["inicio"] < "13:30" and b["fim"] > "12:00")

    def test_sem_tarefas_nao_gera_bloco(self):
        self.assertEqual(diario.dividir_horarios([]), [])


class TarefasDaBranch(unittest.TestCase):
    def test_numero_unico(self):
        self.assertEqual(diario.tarefas_da_branch("feat/69-coluna-setor"), [69])

    def test_varios_numeros(self):
        self.assertEqual(diario.tarefas_da_branch("feat/62-63-relatorio-fiscalizacao-api"), [62, 63])

    def test_numero_como_segmento(self):
        self.assertEqual(diario.tarefas_da_branch("feat/223/comparar-faixa-CID"), [223])

    def test_sem_numero(self):
        self.assertEqual(diario.tarefas_da_branch("hotfix/ajustar-filtros"), [])

    def test_numero_no_meio_do_nome_nao_conta(self):
        self.assertEqual(diario.tarefas_da_branch("feat/novidades-v2"), [])


class Classificar(unittest.TestCase):
    def test_pr_aberta_vence_tudo(self):
        self.assertEqual(diario.classificar({"push", "pr_aberta", "review"}), diario.OBS_PR_ABERTA)

    def test_push_apos_review_recebida(self):
        self.assertEqual(diario.classificar({"push_apos_review", "review"}), diario.OBS_MUDANCAS_PR)

    def test_so_review(self):
        self.assertEqual(diario.classificar({"review"}), diario.OBS_REVIEW)

    def test_push_sem_pr(self):
        self.assertEqual(diario.classificar({"push", "branch_criada"}), diario.OBS_IMPLEMENTACAO)

    def test_so_card_ou_branch_criada(self):
        self.assertEqual(diario.classificar({"comentario_card", "branch_criada"}), diario.OBS_ANALISE)


class Agrupar(unittest.TestCase):
    def mov(self, atividade, tipo, hora):
        return {"atividade": {"id": atividade, "title": "Tarefa %s" % atividade}, "contexto": {},
                "tipo": tipo, "quando": dt.datetime(2026, 11, 10, hora)}

    def test_card_so_alterado_e_ignorado_quando_ha_evidencia_forte(self):
        tarefas = diario.agrupar([self.mov("61", "card_alterado", 9), self.mov("67", "push", 10)])
        self.assertEqual([t["atividade"]["id"] for t in tarefas], ["67"])

    def test_card_so_alterado_conta_quando_e_a_unica_evidencia(self):
        tarefas = diario.agrupar([self.mov("61", "card_alterado", 9)])
        self.assertEqual([t["atividade"]["id"] for t in tarefas], ["61"])

    def test_ordem_pelo_primeiro_evento(self):
        tarefas = diario.agrupar([self.mov("B", "push", 15), self.mov("A", "push", 9), self.mov("B", "pr_aberta", 8)])
        self.assertEqual([t["atividade"]["id"] for t in tarefas], ["B", "A"])


class DiaUtilAnterior(unittest.TestCase):
    def test_segunda_volta_para_sexta(self):
        self.assertEqual(diario.dia_util_anterior(dt.date(2026, 11, 9)), dt.date(2026, 11, 6))

    def test_terca_volta_para_segunda(self):
        self.assertEqual(diario.dia_util_anterior(dt.date(2026, 11, 10)), dt.date(2026, 11, 9))


if __name__ == "__main__":
    unittest.main()
