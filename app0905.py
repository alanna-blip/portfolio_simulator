import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import numpy as np
import json
import requests
import hashlib # 用於密碼雜湊
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe

# --- 頁面設定 ---
st.set_page_config(page_title="美股智能投顧", layout="wide")

# --- Google Sheets 連線 (使用 gspread) ---
@st.cache_resource
def connect_to_gsheets():
    """建立並快取 gspread 的連線"""
    try:
        # 從 Streamlit secrets 讀取憑證
        creds = st.secrets["gspread_credentials"]
        # 使用服務帳戶憑證進行授權
        gc = gspread.service_account_from_dict(creds)
        # 從 secrets 讀取試算表 URL 並開啟
        spreadsheet_url = st.secrets["gspread_spreadsheet"]["url"]
        sh = gc.open_by_url(spreadsheet_url)
        return sh
    except Exception as e:
        st.error(f"無法連接到 Google Sheets，請檢查您的 secrets 設定: {e}")
        return None

# 建立連線
spreadsheet = connect_to_gsheets()

# --- Gemini API 函數 (已修改) ---
def get_gemini_recommendation(prompt, api_key):
    """發送請求到 Gemini API 並獲取投資建議。"""
    # 增加 maxOutputTokens 以避免回應被截斷
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "topK": 1, "topP": 1, "maxOutputTokens": 4096} # 增加輸出長度
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        
        # --- 錯誤修正：加入更穩健的解析邏輯 ---
        candidates = result.get("candidates")
        if not candidates:
            st.error("AI 回應中找不到 'candidates'。")
            st.json(result)
            return None
            
        content = candidates[0].get("content")
        if not content:
            finish_reason = candidates[0].get("finishReason", "未知")
            st.error(f"AI 回應因 '{finish_reason}' 而不完整，找不到 'content'。")
            st.json(result)
            return None
            
        parts = content.get("parts")
        if not parts:
            st.error("AI 回應中找不到 'parts'，內容可能為空。")
            st.json(result)
            return None
            
        return parts[0]['text']

    except requests.exceptions.RequestException as e:
        st.error(f"呼叫 Gemini API 時發生網路錯誤: {e}")
        return None
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        st.error(f"解析 Gemini API 回應時發生錯誤: {e}")
        st.text("原始回應:")
        st.code(response.text) # 顯示原始文字以利除錯
        return None

# --- 使用者身份驗證輔助函數 ---
def hash_password(password):
    """對密碼進行 SHA256 雜湊處理"""
    return hashlib.sha256(password.encode()).hexdigest()

def get_users_df():
    """從 Google Sheets 讀取所有使用者資料"""
    try:
        users_ws = spreadsheet.worksheet("users")
        df = get_as_dataframe(users_ws, evaluate_formulas=True)
        if not df.empty:
            df = df.astype(str)
        return df
    except gspread.WorksheetNotFound:
        st.error("找不到名為 'users' 的工作表，請檢查您的 Google Sheet 設定。")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"讀取使用者資料時發生錯誤: {e}")
        return pd.DataFrame()


# --- 頁面邏輯 ---
if 'user' not in st.session_state:
    st.session_state['user'] = None
    st.session_state['page'] = '登入'

def page_login():
    """顯示登入與註冊頁面"""
    st.title("歡迎使用美股智能投顧")
    st.caption("技術核心：Google Gemini AI | 資料庫：Google Sheets")
    st.write("請登入或註冊以繼續")

    choice = st.selectbox("選擇操作", ["登入", "註冊"])

    if not spreadsheet:
        st.warning("資料庫未連接，無法進行登入或註冊。")
        return

    if choice == "登入":
        with st.form("login_form"):
            email = st.text_input("電子郵件")
            password = st.text_input("密碼", type="password")
            submit_button = st.form_submit_button("登入")

            if submit_button:
                if not email or not password:
                    st.warning("請輸入電子郵件和密碼。")
                    return
                users_df = get_users_df()
                if users_df.empty and 'email' not in users_df.columns:
                     st.error("使用者資料表格式不正確或為空。")
                     return

                user_record = users_df[users_df['email'] == email]

                if not user_record.empty:
                    stored_hash = user_record.iloc[0]['hashed_password']
                    if hash_password(password) == stored_hash:
                        st.session_state['user'] = {
                            'email': user_record.iloc[0]['email'],
                            'display_name': user_record.iloc[0]['display_name']
                        }
                        st.session_state['page'] = '主頁'
                        st.success(f"歡迎回來, {st.session_state['user']['display_name']}！")
                        st.rerun()
                    else:
                        st.error("密碼錯誤。")
                else:
                    st.error("此用戶不存在。")

    else: # 註冊
        with st.form("signup_form"):
            email = st.text_input("電子郵件")
            password = st.text_input("密碼", type="password")
            display_name = st.text_input("暱稱")
            submit_button = st.form_submit_button("註冊")

            if submit_button:
                if not email or not password or not display_name:
                    st.warning("請填寫所有欄位。")
                    return

                users_df = get_users_df()
                if 'email' in users_df.columns and email in users_df['email'].values:
                    st.error("此電子郵件已被註冊。")
                else:
                    hashed = hash_password(password)
                    new_user_data = pd.DataFrame([[email, hashed, display_name]], columns=['email', 'hashed_password', 'display_name'])
                    updated_df = pd.concat([users_df, new_user_data], ignore_index=True)

                    try:
                        users_ws = spreadsheet.worksheet("users")
                        set_with_dataframe(users_ws, updated_df)
                        st.success("註冊成功！請前往登入頁面登入。")
                    except Exception as e:
                        st.error(f"寫入使用者資料時發生錯誤: {e}")


def page_main():
    """應用程式主頁面"""
    user_name = st.session_state.user.get('display_name', '訪客')
    st.sidebar.header(f"👋 你好, {user_name}")
    if st.sidebar.button("登出"):
        st.session_state['user'] = None
        st.session_state['page'] = '登入'
        st.rerun()

    st.title("📈 美股智能投顧")
    st.caption("AI 模型版本: Google Gemini `gemini-2.5-flash-preview-05-20`")

    load_dotenv()
    gemini_api_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY")

    if not gemini_api_key:
        st.error("偵測不到 GEMINI_API_KEY！請在 .env 檔案或 Streamlit Secrets 中設定。")
        return

    # --- 側邊欄輸入 ---
    with st.sidebar:
        st.header("📋 基本個人資訊")
        professions = ["辦公室職員", "服務業", "製造業", "公務員", "學生", "自由工作者", "其他"]
        profession = st.selectbox("職業", professions)
        salary_ranges = ["2萬以下", "2萬-4萬", "4萬-6萬", "6萬-8萬", "8萬以上"]
        monthly_salary = st.selectbox("月薪範圍（台幣）", salary_ranges)
        debt_ranges = ["無負債", "10萬以下", "10萬-50萬", "50萬-100萬", "100萬-500萬", "500萬以上"]
        debt = st.selectbox("負債範圍（台幣）", debt_ranges)
        age_ranges = ["20歲以下", "20-30歲", "30-40歲", "40-50歲", "50歲以上"]
        age_range = st.selectbox("年齡範圍", age_ranges)
        st.header("📝 風險偏好與經驗")
        risk_tolerances = ["保守型", "均衡型", "積極型"]
        risk_tolerance = st.selectbox("風險偏好", risk_tolerances)
        investment_experiences = ["無經驗", "1年以下", "1-3年", "3年以上"]
        investment_experience = st.selectbox("投資經驗", investment_experiences)


    # --- 主內容區 ---
    tab1, tab2, tab3, tab4 = st.tabs(["🤖 AI 投資建議", "📈 歷史推薦績效", "🏦 一站式開戶", "📚 投資教育中心"])

    with tab1:
        st.header("獲取您的專屬投資組合")
        if st.button("🚀 開始分析"):
            with st.spinner("AI 正在為您客製化分析中..."):
                prompt = f"""
                作為一名專業的財富顧問，請根據以下使用者資料，為一位投資新手推薦3到5個在美國市場的投資標的（可以是股票或ETF）。
                您的推薦需要考慮到風險分散、使用者的財務狀況與風險偏好。

                使用者資料:
                - 職業: {profession}
                - 月薪範圍: {monthly_salary} (台幣)
                - 負債範圍: {debt} (台幣)
                - 年齡範圍: {age_range}
                - 風險偏好: {risk_tolerance}
                - 投資經驗: {investment_experience}

                請嚴格按照以下格式回覆，不要有任何多餘的文字或解釋:
                [START]
                推薦理由: [在這裡用繁體中文，不超過150字，簡潔地解釋為什麼推薦這個組合]
                股票代碼: [以逗號分隔的股票代碼，例如：VOO,AAPL,MSFT]
                投資比例: [以逗號分隔的數字，總和必須為1，例如：0.6,0.2,0.2]
                [END]
                """

                response_content = get_gemini_recommendation(prompt, gemini_api_key)

                if response_content:
                    st.write("---")
                    st.subheader("🤖 AI 客製化推薦")
                    try:
                        content = response_content.split("[START]")[1].split("[END]")[0].strip()
                        lines = content.split('\n')
                        reason = lines[0].replace("推薦理由: ", "").strip()
                        tickers_line = lines[1].replace("股票代碼: ", "").strip()
                        weights_line = lines[2].replace("投資比例: ", "").strip()

                        tickers = [t.strip() for t in tickers_line.split(",")]
                        weights = [float(w.strip()) for w in weights_line.split(",")]

                        st.info(f"**AI 推薦理由：** {reason}")

                        if len(tickers) != len(weights) or not np.isclose(sum(weights), 1.0):
                             st.error("AI 回應的格式有誤（代碼與權重數量不符或權重總和不為1），請再試一次。")
                        else:
                            display_portfolio_performance(tickers, weights, reason, gemini_api_key)

                            if spreadsheet:
                                user_email = st.session_state.user['email']
                                recs_ws = spreadsheet.worksheet("recommendations")
                                recs_df = get_as_dataframe(recs_ws).astype(str)
                                new_rec = pd.DataFrame([{
                                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    'user_email': user_email,
                                    'tickers': ','.join(tickers),
                                    'weights': ','.join(map(str, weights)),
                                    'reason': reason
                                }])
                                updated_df = pd.concat([recs_df, new_rec], ignore_index=True)
                                set_with_dataframe(recs_ws, updated_df)
                                st.success("這次的推薦已成功儲存！您可以在「歷史推薦績效」分頁查看。")

                    except Exception as e:
                        st.error(f"解析 AI 回應或儲存紀錄時失敗，請重試。錯誤訊息：{e}")
                        st.text("AI 原始回應:")
                        st.code(response_content)

    with tab2:
        st.header("查看您過去的 AI 推薦與即時績效")
        if not spreadsheet:
            st.warning("資料庫未連接，無法讀取歷史紀錄。")
        else:
            user_email = st.session_state.user['email']
            recs_ws = spreadsheet.worksheet("recommendations")
            all_recs_df = get_as_dataframe(recs_ws).astype(str)

            if 'user_email' in all_recs_df.columns:
                user_recs_df = all_recs_df[all_recs_df['user_email'] == user_email].sort_values(by='timestamp', ascending=False)
            else:
                user_recs_df = pd.DataFrame()

            if user_recs_df.empty:
                st.info("您目前沒有任何歷史推薦紀錄。")
            else:
                for i, rec in user_recs_df.iterrows():
                    timestamp = rec['timestamp']
                    tickers = rec['tickers'].split(',')
                    weights = [float(w) for w in rec['weights'].split(',')]
                    reason = rec['reason']
                    with st.expander(f"**{timestamp}** 的推薦組合：`{rec['tickers']}`"):
                        st.info(f"**當時的推薦理由：** {reason}")
                        st.write("---")
                        display_portfolio_performance(tickers, weights, reason, gemini_api_key, is_historical=True)

    with tab3:
        # ... (內容無變更) ...
        st.header("🇹🇼 投資美股第一步：選擇適合的台灣券商")
        st.markdown("""
        在台灣投資美股，最常見的方式是透過國內券商的「複委託」服務。這代表您委託台灣的券商，再去美國的券商下單。
        以下推薦幾家對新手友善、手續費有競爭力的券商，幫助您輕鬆開始。
        """)
        st.subheader("1. 永豐金證券 (SinoPac Securities)")
        st.markdown("""
        - **主要特色**:
            - **豐存股-美股**: 提供定期定額/定股功能，可以一股一股或小額買入美股，非常適合小資族。
            - **數位帳戶整合**: 與自家大戶 (DAWHO) 數位銀行帳戶整合度高，資金進出方便。
            - **手續費**: 網路下單手續費具競爭力，且常有優惠活動。
        - **適合對象**: 喜歡定期定額、小額投資的年輕族群與數位帳戶使用者。
        - **[➡️ 前往永豐金證券官網](https://www.sinotrade.com.tw/)**
        """)
        st.subheader("2. 富邦證券 (Fubon Securities)")
        st.markdown("""
        - **主要特色**:
            - **市佔率高**: 為台灣最大的券商之一，系統穩定，服務據點多。
            - **手續費優惠**: 網路下單手續費低廉，是市場上的領先者之一。
            - **一戶通**: 整合台股與複委託帳戶，資金管理方便。
        - **適合對象**: 追求低手續費、希望有實體據點可諮詢的投資人。
        - **[➡️ 前往富邦證券官網](https://www.fubon.com/securities/)**
        """)
        st.subheader("3. 國泰證券 (Cathay Securities)")
        st.markdown("""
        - **主要特色**:
            - **App 介面友善**: 國泰證券 App 操作直覺，使用者體驗佳。
            - **定期定股**: 同樣提供美股定期定股功能，方便長期投資。
            - **集團資源**: 隸屬國泰金控，可與銀行、保險等服務結合。
        - **適合對象**: 重視 App 操作體驗、國泰集團的既有客戶。
        - **[➡️ 前往國泰證券官網](https://www.cathaysec.com.tw/)**
        """)
        st.warning("**溫馨提醒**: 各家券商的手續費與優惠活動時常變動，開戶前請務必前往官方網站，確認最新的費率與開戶詳情。")

    with tab4:
        # ... (內容無變更) ...
        st.header("📚 投資教育中心：打好您的理財基礎")
        education_options = [
            "ETF 是什麼？", "股票風險如何評估？", "多元化投資的重要性",
            "手續費與交易成本", "長期投資的優勢", "如何閱讀財務報表"
        ]
        selected_education = st.selectbox("選擇您想學習的主題", education_options)

        if selected_education == "ETF 是什麼？":
            st.markdown("""
            **ETF (Exchange-Traded Fund)，中文是「指數股票型基金」**，是一種在股票交易所買賣的基金。

            您可以把它想像成一個「**投資組合懶人包**」。基金公司先幫您買好一籃子的資產（例如數十支甚至數百支股票或債券），然後將這個籃子分成很多份，讓您可以像買賣單一股票一樣，輕鬆地買賣一小份。

            - **優點**:
                - **自動分散風險**: 買一個追蹤大盤的 ETF (如 VOO)，就等於一次投資了美國 500 家大公司，避免單一公司暴跌的風險。
                - **低成本**: 管理費用通常遠低於傳統的主動型基金，長期下來可以省下可觀的成本。
                - **高透明度**: 您隨時可以知道這個「籃子」裡到底裝了哪些股票。
            - **範例**: VOO (追蹤美國 S&P 500 指數), QQQ (追蹤納斯達克 100 指數), VT (追蹤全球市場)。
            """)
        elif selected_education == "股票風險如何評估？":
            st.markdown("""
            評估股票風險沒有單一的完美指標，但您可以從以下幾個角度來綜合判斷，當個聰明的投資人：

            - **波動性 (Volatility)**: 指股價上下起伏的劇烈程度。通常用「標準差」來衡量。波動越大的股票，風險越高，但也可能帶來更高回報。您可以在財經網站上看到一支股票的歷史波動率。
            - **Beta (β) 值**: 衡量一支股票相對於整個市場（如 S&P 500 指數）的波動性。
                - Beta > 1: 代表股價波動比大盤更劇烈。
                - Beta = 1: 代表與大盤同步。
                - Beta < 1: 代表股價波動比大盤更平穩。
            - **公司基本面**: 風險不僅僅是股價波動。公司的財務狀況（是否賺錢？負債高不高？）、產業前景、競爭力等，都是更根本的風險來源。一家持續虧損的公司，風險自然很高。
            - **新手建議**: 剛開始可以從大型、穩定獲利、產業龍頭的公司或大盤 ETF 入手，它們的風險通常較低。
            """)
        elif selected_education == "多元化投資的重要性":
            st.markdown("""
            **「不要把所有雞蛋放在同一個籃子裡。」** 這句古老的諺語，完美詮釋了多元化投資的核心精神。

            多元化是指將您的資金分配到不同類型、不同產業、不同地區的資產中，目的是**分散風險**。

            - **為什麼重要？**:
                - **降低衝擊**: 很少有所有資產「同時」大跌的情況。當您的科技股下跌時，或許您投資的民生消費股正在上漲，這樣一來一往，您的整體投資組合就不會受到毀滅性的打擊。
                - **平滑報酬**: 多元化可以幫助您獲得更穩定的長期回報，避免投資組合像坐雲霄飛車一樣大起大落，讓您能抱得更安穩。
            - **如何做到？**:
                - **跨資產**: 同時持有股票和債券。
                - **跨產業**: 投資組合中應包含科技、金融、醫療、消費等多個不同產業的股票。
                - **跨地區**: 除了美股，也可以考慮投資其他國家市場的 ETF。
            - **最簡單的方式**: 對新手而言，直接買入一檔全球市場 ETF (如 VT) 或美國大盤 ETF (如 VOO)，本身就是一種極佳的多元化策略。
            """)
        elif selected_education == "手續費與交易成本":
            st.markdown("""
            **手續費是侵蝕您獲利的隱形殺手！** 即使是很小的費用，在長期複利效應下，也會對您的最終回報產生巨大影響。

            在台灣透過複委託投資美股，主要會遇到以下成本：

            - **券商手續費**:
                - **買入/賣出費用**: 這是最主要的成本。通常是成交金額的一個百分比（例如 0.25%），並且會設有「最低收費」（例如 15 美元）。
                - **優惠活動**: 許多券商會提供手續費折扣或降低最低收費的優惠，下單前一定要多加比較。
            - **其他潛在費用**:
                - **電匯費**: 將資金匯到海外或從海外匯回時，銀行會收取費用。
                - **交易所費**: 非常小額，通常已內含在券商費用中。
            - **重點提醒**: 對於小額投資人來說，「最低收費」的影響最大。如果您的單筆交易金額不高，高昂的最低收費會吃掉您大部分的獲利。這也是為什麼永豐金的「豐存股」等定期定額服務對小資族很有吸引力，因為它們通常有更優惠的計費方式。
            """)
        elif selected_education == "長期投資的優勢":
            st.markdown("""
            股神巴菲特曾說：「如果你不打算持有一支股票十年，那連十分鐘都不要持有。」這句話揭示了長期投資的強大之處。

            - **享受複利效應**: 愛因斯坦稱之為「世界第八大奇蹟」。您的投資不僅本金會增長，連同獲利本身也會在未來繼續產生新的獲利，就像滾雪球一樣，時間越長，雪球滾得越大。
            - **穿越市場波動**: 短期市場的漲跌非常難以預測，充滿了各種雜訊。但拉長時間看，優質資產的價格趨勢通常是向上的。長期投資讓您可以忽略短期的紛擾，專注於分享經濟增長的果實。
            - **降低擇時風險**: 試圖「買在最低點、賣在最高點」是多數專業人士都做不到的事。長期投資（例如定期定額）採用「時間換取空間」的策略，讓您不必為猜測市場時機而焦慮。
            - **歷史數據**: 歷史上，即使您不幸買在市場最高點，只要堅持長期持有美國 S&P 500 指數超過 10-15 年，獲得正報酬的機率非常高。
            """)
        elif selected_education == "如何閱讀財務報表":
            st.markdown("""
            財務報表是公司的「體檢報告」，雖然看起來複雜，但新手可以從理解三大核心報表的基本功能開始：

            1.  **損益表 (Income Statement)**:
                - **功能**: 告訴您公司在「一段時間內」（例如一季或一年）是**賺錢還是虧錢**。
                - **關鍵項目**:
                    - **營收 (Revenue)**: 公司賣出商品或服務賺到的總金額。
                    - **淨利 (Net Income)**: 營收扣掉所有成本、費用和稅務後，真正進到口袋的錢。淨利是否穩定增長，是判斷公司好壞的關鍵。

            2.  **資產負債表 (Balance Sheet)**:
                - **功能**: 像一張「快照」，告訴您在「某個時間點」，公司**有多少資產、欠了多少債**。
                - **核心公式**: **資產 (Assets) = 負債 (Liabilities) + 股東權益 (Equity)**
                - **新手看點**: 比較一下公司的總資產和總負債。如果負債比例過高，可能代表財務風險較大。

            3.  **現金流量表 (Cash Flow Statement)**:
                - **功能**: 追蹤在「一段時間內」，公司**現金的流入與流出**情況。
                - **為什麼重要**: 一家公司可能帳面上賺錢（淨利為正），但如果收不回現金，最終還是會倒閉。這張表反映了公司真實的營運健康狀況。
            - **去哪裡看**: 您可以在 Yahoo Finance 或券商 App 中，輕鬆找到上市公司的免費財務報表。
            """)


# --- 蒙地卡羅模擬函數 (從 display_portfolio_performance 中分離出來) ---
def run_monte_carlo_simulation(portfolio_returns, api_key, tickers):
    """執行並顯示蒙地卡羅模擬的結果與 AI 解說。"""
    with st.spinner("正在執行 1,000 次未來路徑模擬..."):
        # 模擬參數
        n_simulations = 1000
        years = 10
        initial_investment = 10000 # 假設初始投資一萬美元
        
        # 計算日報酬率的平均值與標準差
        mean_return = portfolio_returns.mean()
        std_dev = portfolio_returns.std()
        
        # 產生模擬數據
        simulated_returns = np.random.normal(mean_return, std_dev, (252 * years, n_simulations))
        cumulative_sim_returns = (1 + pd.DataFrame(simulated_returns)).cumprod()
        final_portfolio_values = initial_investment * cumulative_sim_returns.iloc[-1]
        
        # 繪製箱型圖
        st.subheader("十年後投資價值分佈預測")
        fig_sim = px.box(y=final_portfolio_values, points="all", 
                         title=f"基於過去兩年數據模擬一萬美元投資十年後的價值分佈")
        fig_sim.update_layout(yaxis_title="投資組合價值 (美元)", xaxis_title="推薦組合")
        st.plotly_chart(fig_sim, use_container_width=True)
        
        # 計算與顯示預測結果
        percentiles = np.percentile(final_portfolio_values, [5, 50, 95])
        st.markdown(f"""
        - **中位數價值 (50% 機率)**: 10 年後，您的 ${initial_investment:,.0f} 投資，有 50% 的機率會成長到 **${percentiles[1]:,.0f}** 美元以上。
        - **90% 信心區間**: 我們有 90% 的信心，10 年後的投資價值會落在 **${percentiles[0]:,.0f}** 美元至 **${percentiles[2]:,.0f}** 美元之間。
        """)
        
        # 產生 AI 解說
        st.subheader("🤖 AI 解說模擬結果")
        with st.spinner("AI 正在為您解讀風險預測圖表..."):
            sim_explanation_prompt = f"""
            請以一位親切的理財顧問的身份，用繁體中文、簡單易懂的語言（約150-200字），對一位投資新手解釋以下的「10年期蒙地卡羅模擬」結果。

            模擬情境:
            - 投資組合: {tickers}
            - 初始投資: ${initial_investment:,.0f} 美元

            模擬結果:
            - 10年後投資價值的中位數: ${percentiles[1]:,.0f} 美元
            - 90%信心區間: ${percentiles[0]:,.0f} 美元至 ${percentiles[2]:,.0f} 美元之間。

            請根據以上數據，解釋箱型圖（Box Plot）所代表的意義（它顯示了上千種可能的未來結果），並說明信心區間的實際意涵（未來財富的可能範圍）。最後用一句話總結長期投資的潛力與不確定性。請勿提供任何新的投資建議。
            """
            sim_explanation = get_gemini_recommendation(sim_explanation_prompt, api_key)
            if sim_explanation:
                st.info(sim_explanation)
            else:
                st.warning("無法生成 AI 解說。")

# --- 績效與風險預測主函數 (已修改) ---
def display_portfolio_performance(tickers, weights, reason, api_key, is_historical=False):
    """下載數據、計算績效、執行蒙地卡羅模擬並顯示圖表與 AI 解說。"""
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=2*365)
        title_prefix = "歷史推薦組合" if is_historical else "AI 推薦組合"

        subheader_title = f"📈 {title_prefix} - 標的歷史績效"
        if is_historical:
            subheader_title += f" (回測區間: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')})"

        rec_data = yf.download(tickers, start=start_date, end=end_date, auto_adjust=True)["Close"]

        if isinstance(rec_data, pd.Series):
            rec_data = rec_data.to_frame(name=tickers[0])
        if rec_data.empty:
            st.warning("⚠️ 在指定日期範圍內找不到有效的歷史數據。")
            return

        rec_data = rec_data.ffill().bfill()
        if rec_data.isnull().values.any():
             st.warning("部分股票數據缺失，可能影響計算準確性。")
             rec_data.dropna(inplace=True)

        st.subheader(subheader_title)
        normalized_data = rec_data / rec_data.iloc[0]
        fig_rec = px.line(normalized_data, title=f"{title_prefix} - 價格走勢 (標準化)")
        st.plotly_chart(fig_rec, use_container_width=True)

        returns = rec_data.pct_change().dropna()
        portfolio_returns = (returns * weights).sum(axis=1)
        cumulative_returns = (1 + portfolio_returns).cumprod()

        st.subheader(f"💼 {title_prefix} - 累積報酬")
        fig_cum = px.line(x=cumulative_returns.index, y=cumulative_returns, title=f"{title_prefix} - 累積報酬率")
        fig_cum.update_layout(yaxis_title="累積報酬", xaxis_title="日期")
        st.plotly_chart(fig_cum, use_container_width=True)

        total_return = cumulative_returns.iloc[-1] - 1
        days = (end_date - start_date).days
        annual_return = ((1 + total_return) ** (365.0 / days)) - 1 if days > 0 else 0
        annual_volatility = portfolio_returns.std() * np.sqrt(252)
        sharpe_ratio = (annual_return - 0.02) / annual_volatility if annual_volatility != 0 else 0

        st.subheader("📊 績效總覽")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("期間總報酬率", f"{total_return:.2%}")
        col2.metric("年化報酬率", f"{annual_return:.2%}")
        col3.metric("年化波動率", f"{annual_volatility:.2%}")
        col4.metric("夏普比率", f"{sharpe_ratio:.2f}")
        
        st.write("---")
        
        # --- 錯誤修正：條件化顯示 expander ---
        if not is_historical:
            # 在「AI投資建議」分頁，使用 expander 來節省空間
            with st.expander("🎲 查看未來10年投資組合風險預測 (蒙地卡羅模擬)"):
                run_monte_carlo_simulation(portfolio_returns, api_key, tickers)
        else:
            # 在「歷史推薦績效」分頁，直接顯示以避免巢狀 expander 錯誤
            st.subheader("🎲 未來10年投資組合風險預測 (蒙地卡羅模擬)")
            run_monte_carlo_simulation(portfolio_returns, api_key, tickers)

    except Exception as e:
        st.error(f"⚠️ 數據處理或圖表生成失敗: {e}")
        st.info("可能是因為股票代碼有誤或 Yahoo Finance 暫時無法提供數據。")

# --- 主應用程式路由 ---
if st.session_state.get('page', '登入') == '登入':
    page_login()
else:
    page_main()

