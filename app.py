import os
import psycopg2
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google import genai

app = Flask(__name__)

# Pega a URL do banco do Supabase e a chave da API do Gemini das variáveis de ambiente do Render
DB_URL = os.environ.get("DATABASE_URL")
ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def get_db():
    return psycopg2.connect(DB_URL)

@app.route('/bot', methods=['POST'])
def bot():
    msg = request.form.get('Body', '').strip()
    user_phone = request.form.get('From', '').strip()  # Identificador do número do WhatsApp
    resp = MessagingResponse()
    reply = resp.message()

    conn = get_db()
    cursor = conn.cursor()

    # -------------------------------------------------------------
    # COMANDO 1: Cadastrar nova questão
    # Formato: /add | Matéria | Enunciado com opções | Gabarito
    # -------------------------------------------------------------
    if msg.lower().startswith('/add'):
        try:
            parts = msg.split('|')
            if len(parts) >= 4:
                materia = parts[1].strip()
                enunciado = parts[2].strip()
                gabarito = parts[3].strip().upper()

                cursor.execute(
                    "INSERT INTO meubanco (materia, enunciado, gabarito) VALUES (%s, %s, %s)",
                    (materia, enunciado, gabarito)
                )
                conn.commit()
                reply.body("✅ Questão salva com sucesso na sua memória!")
            else:
                reply.body("⚠️ Formato incorreto. Use:\n`/add | Matéria | Enunciado | Gabarito`")
        except Exception as e:
            reply.body("❌ Erro ao salvar questão. Verifique a estrutura enviada.")

    # -------------------------------------------------------------
    # COMANDO 2: Sortear uma questão da memória
    # Formato: /questao
    # -------------------------------------------------------------
    elif msg.lower().startswith('/questao'):
        cursor.execute("SELECT id, materia, enunciado FROM meubanco ORDER BY RANDOM() LIMIT 1")
        row = cursor.fetchone()

        if row:
            q_id, materia, enunciado = row
            
            # Salva no histórico a última questão enviada para este número de telefone
            cursor.execute("""
                INSERT INTO historico_usuario (telefone, ultima_questao_id)
                VALUES (%s, %s)
                ON CONFLICT (telefone) 
                DO UPDATE SET ultima_questao_id = EXCLUDED.ultima_questao_id
            """, (user_phone, q_id))
            conn.commit()

            reply.body(
                f"📌 *Questão #{q_id} [{materia}]*\n\n"
                f"{enunciado}\n\n"
                f"👉 *Para responder:* Envie apenas a letra (ex: *A*, *B*, *C*, *D* ou *E*)\n"
                f"💡 *Para dica:* Envie `/dica`"
            )
        else:
            reply.body("Nenhuma questão cadastrada ainda. Use `/add` para salvar suas primeiras questões!")

    # -------------------------------------------------------------
    # COMANDO 3: Pedir dica da questão ativa
    # Formato: /dica
    # -------------------------------------------------------------
    elif msg.lower().startswith('/dica'):
        cursor.execute("""
            SELECT m.enunciado, m.gabarito 
            FROM meubanco m
            JOIN historico_usuario h ON m.id = h.ultima_questao_id
            WHERE h.telefone = %s
        """, (user_phone,))
        row = cursor.fetchone()

        if row:
            enunciado, gabarito = row
            prompt = (
                f"Questão: '{enunciado}'\n"
                f"Gabarito: '{gabarito}'\n"
                f"Forneça apenas um spoiler conceitual ou dica estratégica para ajudar o aluno "
                f"a pensar na resolução por conta própria. Não revele a resposta ou gabarito diretamente."
            )
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            reply.body(f"💡 *Dica:* {response.text}")
        else:
            reply.body("Você não tem nenhuma questão ativa no momento. Envie `/questao` primeiro!")

    # -------------------------------------------------------------
    # VERIFICAÇÃO DE RESPOSTA (Se enviar apenas A, B, C, D ou E)
    # -------------------------------------------------------------
    elif len(msg) == 1 and msg.upper() in ['A', 'B', 'C', 'D', 'E']:
        resposta_usuario = msg.upper()
        
        cursor.execute("""
            SELECT m.id, m.gabarito, m.enunciado 
            FROM meubanco m
            JOIN historico_usuario h ON m.id = h.ultima_questao_id
            WHERE h.telefone = %s
        """, (user_phone,))
        row = cursor.fetchone()

        if row:
            q_id, gabarito_correto, enunciado = row
            
            # Verifica se a letra enviada coincide com o gabarito
            if resposta_usuario in gabarito_correto.upper():
                reply.body(f"🎉 *Parabéns! Você acertou!*\nA alternativa correta da Questão #{q_id} é a letra *{resposta_usuario}*.")
            else:
                prompt = (
                    f"O aluno errou a Questão #{q_id}.\n"
                    f"Enunciado: {enunciado}\n"
                    f"Gabarito correto: {gabarito_correto}\n"
                    f"Resposta marcou: {resposta_usuario}\n"
                    f"Explique sucintamente o erro conceitual comum ao escolher essa alternativa incorreta."
                )
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                )
                reply.body(f"❌ *Incorreto!* Você marcou a alternativa *{resposta_usuario}*.\n\n{response.text}")
        else:
            reply.body("Nenhuma questão ativa encontrada. Envie `/questao` para receber um desafio.")

    # -------------------------------------------------------------
    # MENSAGEM PADRÃO / AJUDA
    # -------------------------------------------------------------
    else:
        reply.body(
            "🤖 *Bot de Questões*\n\n"
            "Comandos disponíveis:\n"
            "• `/add | Matéria | Enunciado | Gabarito`\n"
            "• `/questao` (sorteia uma questão)\n"
            "• `/dica` (pede auxílio da questão ativa)\n"
            "• Responda enviando apenas a letra (*A*, *B*, *C*, *D*, *E*)"
        )

    cursor.close()
    conn.close()
    return str(resp)

if __name__ == '__main__':
    app.run(port=5000)
