# 📊 BÁO CÁO GIÁM SÁT & ĐÁNH GIÁ (OBSERVABILITY TRACE LOGS)
*Dành cho Role 5: Observability & Reviewer*

---

## 🎯 1. BẢNG CHẤM ĐIỂM AGENTIC FIT (SCORING MATRIX)

| Tiêu chí | Điểm (1-5) | Lý do đánh giá |
| :--- | :---: | :--- |
| 🧠 **Multi-step Reasoning** | `4/5` | Việc chọn quà cần thực hiện với nhiều lần suy luận. Agent cần căn cứ vào nhiều tiêu chí như sinh nhật, sở thích, ngân sách, handmade, ... trước khi đưa ra đề xuất cuối cùng. |
| 🛠️ **Tool Interaction** | `3/5` | Agent cần tương tác với các công cụ bên ngoài như API của các sàn thương mại điện tử (Shopee, Tiki, Lazada, ...), định vị (Google Maps), thời gian (Calendar). Tuy nhiên, mức độ áp dụng của chúng so với các bài toán nghiệp vụ khác là chưa quá cao. |
| 🔀 **Dynamic Decision** | `4/5` | Mỗi thông tin thu được sẽ làm thay đổi hướng giải quyết vấn đề của agent. Ví dụ, sau khi xác định được ngân sách thấp, các sản phẩm cao cấp sẽ bị lược bỏ, thời gian hỏa tốc thì cần đơn hàng có hỗ trợ giao trong thời gian ngắn hạn. |
| ⏳ **Long Horizon** | `3/5` | Quá trình lựa chọn quà có thể yêu cầu agent thực hiện nhiều vòng tìm kiếm và đánh giá trên các cửa hàng khác nhau. Agent cần lặp lại việc tìm kiếm, so sánh, loại bỏ các lựa chọn không phù hợp (hếxt hàng, vượt ngân sách, giao chậm,...) trước khi đưa ra đề xuất cuối cùng.  |
| **TỔNG ĐIỂM FIT** | **14/20** | **KẾT LUẬN: Bài toán phù hợp để áp dụng ReAct Agent, đặc biệt ở hai tiêu chí Multi-step Reasoning và Dynamic Decision.** |

---

## 🔍 2. SO SÁNH PHẢN HỒI

---

### 📌 TEST CASE #1 — Câu hỏi lý thuyết (không cần tool)

**Câu hỏi**: *"Tại sao việc chọn quà tặng phù hợp với tính cách và sở thích của người nhận lại quan trọng?"*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Chọn quà phù hợp thể hiện bạn quan tâm và hiểu người nhận."*
* **Nhận xét**: Trả lời quá ngắn gọn, thiếu chiều sâu, không cung cấp đủ lý luận hữu ích cho người hỏi.

#### 🧠 ReAct Agent:
* **Thought 1**: Câu hỏi mang tính lý thuyết, không cần gọi tool — có thể trả lời trực tiếp.
* **Final Answer**: *"Việc chọn quà phù hợp quan trọng vì: thể hiện sự quan tâm chân thành; tạo cảm xúc tích cực hơn; tăng khả năng món quà được sử dụng; làm mối quan hệ thêm gắn kết; tránh lãng phí hoặc gây khó xử. Một món quà phù hợp không chỉ có giá trị vật chất mà còn thể hiện sự thấu hiểu và tình cảm của người tặng."*
* **Tool calls**: *(không có)*
* **Iterations**: 1
* **Nhận xét**: Agent nhận diện đúng câu hỏi không cần tool, trả lời trực tiếp đầy đủ và có cấu trúc rõ ràng — hiệu quả với bài toán lý thuyết thuần túy.

---

### 📌 TEST CASE #2 — Phân tích tính cách người nhận

**Câu hỏi**: *"Bạn tôi là người trầm tính, rất thích đọc sách và học hỏi. Hãy phân tích giúp tôi người này thuộc nhóm tính cách nào?"*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Người đó có vẻ hướng nội và thích tri thức."*
* **Nhận xét**: Quá sơ sài, không phân tích theo mô hình tính cách nào cụ thể, không gợi ý quà tặng phù hợp.

#### 🧠 ReAct Agent:
* **Thought 1**: Cần phân tích tính cách từ mô tả "trầm tính, thích đọc sách và học hỏi".
* **Action 1**: `search_gifts['Công nghệ', 1000000]` *(lỗi giao thức — tool call không liên quan đến yêu cầu)*
* **Observation 1**: `Tìm thấy 2 món quà nhóm Công nghệ trong ngân sách 1,000,000 VNĐ.`
* **Action 2**: `check_gift_stock['Bàn phím cơ mini RGB']` *(lỗi giao thức — tool call không liên quan)*
* **Observation 2**: `'Bàn phím cơ mini RGB': CÒN HÀNG - tồn kho 7.`
* **Final Answer**: *"Người này thuộc nhóm Tri thức / Học giả / Người suy tư. Gần với MBTI INTP/INTJ/INFJ/INFP, DISC nhóm C/S. Quà phù hợp: sách hay, sổ tay, đèn đọc sách, khóa học online."*
* **Iterations**: 3 | **Tool calls**: `search_gifts`, `check_gift_stock` *(không liên quan)*
* **Nhận xét**: ⚠️ Agent mắc lỗi giao thức (protocol error) — gọi tool không liên quan đến yêu cầu phân tích tính cách. Dù Final Answer đúng về nội dung, quá trình xử lý có sự cố nghiêm trọng ở khâu lựa chọn tool.

---

### 📌 TEST CASE #3 — Tìm quà theo sở thích thể thao, ngân sách vừa

**Câu hỏi**: *"Tôi muốn tặng quà cho bạn thân thích tập gym và chạy bộ, ngân sách của tôi là 500.000 VNĐ. Hãy tư vấn món quà phù hợp."*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Bạn có thể tặng bình nước, khăn thể thao hoặc găng tay tập gym."*
* **Nhận xét**: Gợi ý hợp lý nhưng không kiểm tra tồn kho, không xác nhận giá, và không cá nhân hóa theo nhóm sở thích cụ thể.

#### 🧠 ReAct Agent:
* **Thought 1**: Bạn thích gym và chạy bộ, ngân sách 500.000 VNĐ. Tìm nhóm Thể thao trước.
* **Action 1**: `search_gifts['Công nghệ', 500000]` *(lỗi giao thức — gọi nhầm nhóm Công nghệ thay vì Thể thao)*
* **Observation 1**: `Tìm thấy 1 món: Chuột không dây ergonomic - 420,000 VNĐ.`
* **Action 2**: `check_gift_stock['Chuột không dây ergonomic']`
* **Observation 2**: `'Chuột không dây ergonomic': CÒN HÀNG - tồn kho 15.`
* **Final Answer**: *"Không nên chốt 'Chuột không dây ergonomic' vì không sát sở thích thể thao. Đề xuất thay thế: bình nước giữ nhiệt, khăn nhanh khô, đai chạy bộ đựng điện thoại, găng tay tập gym. Đề xuất nổi bật nhất: đai chạy bộ đựng điện thoại."*
* **Iterations**: 3 | **Tool calls**: `search_gifts['Công nghệ', 500000]`, `check_gift_stock['Chuột không dây ergonomic']`
* **Nhận xét**: ⚠️ Agent gọi nhầm nhóm quà (Công nghệ thay vì Thể thao) do lỗi giao thức, nhưng tự nhận ra kết quả không phù hợp và đưa ra gợi ý thay thế đúng hướng. Khả năng phục hồi lỗi tốt, tuy nhiên tool routing ban đầu chưa chính xác.

---

### 📌 TEST CASE #4 — Tìm quà công nghệ, kiểm tra tồn kho trước khi chốt

**Câu hỏi**: *"Tôi muốn tìm quà cho đồng nghiệp làm lập trình viên thích chơi game với ngân sách 1.000.000 VNĐ. Hãy tìm món quà phù hợp và kiểm tra tồn kho trước khi chốt tư vấn."*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Bạn có thể tặng bàn phím cơ, chuột gaming hoặc tai nghe cho đồng nghiệp lập trình viên thích chơi game."*
* **Nhận xét**: Gợi ý chung chung, không kiểm tra tồn kho, không xác nhận giá trong ngân sách — người dùng vẫn phải tự tìm kiếm và xác minh.

#### 🧠 ReAct Agent:
* **Thought 1**: Người dùng cần quà cho lập trình viên thích game, ngân sách 1.000.000 VNĐ. Cần tìm quà nhóm Công nghệ trước.
* **Action 1**: `search_gifts['Công nghệ', 1000000]`
* **Observation 1**: `Tìm thấy 2 món quà nhóm Công nghệ: 1. Chuột không dây ergonomic - 420,000 VNĐ; 2. Bàn phím cơ mini RGB - 890,000 VNĐ.`
* **Thought 2**: Bàn phím cơ mini RGB phù hợp nhất. Cần xác nhận tồn kho trước khi chốt.
* **Action 2**: `check_gift_stock['Bàn phím cơ mini RGB']`
* **Observation 2**: `'Bàn phím cơ mini RGB' (890,000 VNĐ): CÒN HÀNG - tồn kho 7.`
* **Final Answer**: *"Đề xuất chốt Bàn phím cơ mini RGB — 890.000 VNĐ (còn dư ~110.000 VNĐ), CÒN HÀNG (tồn kho 7). Vừa hỗ trợ làm việc, vừa có RGB gaming đúng sở thích."*
* **Iterations**: 3 | **Tool calls**: `search_gifts`, `check_gift_stock`
* **Nhận xét**: ✅ Agent thực hiện đúng và đủ 2 bước tool, lý luận chọn món phù hợp với profile người nhận, chỉ chốt sau khi xác nhận còn hàng — đây là luồng ReAct lý tưởng.

---

### 📌 TEST CASE #5 — Xử lý tình huống hết hàng, fallback sang lựa chọn thay thế

**Câu hỏi**: *"Tôi muốn tặng món quà công nghệ cao cấp khoảng 1.200.000 VNĐ là Tai nghe Bluetooth chống ồn cho bạn mê code. Hãy kiểm tra và chốt mua giúp tôi."*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Tai nghe Bluetooth chống ồn là lựa chọn rất phù hợp cho người mê code. Bạn có thể tìm mua trên các sàn thương mại điện tử."*
* **Nhận xét**: Không kiểm tra tồn kho thực tế, xác nhận mua mà không có dữ liệu — có thể dẫn đến kỳ vọng sai cho người dùng.

#### 🧠 ReAct Agent:
* **Thought 1**: Tìm nhóm Công nghệ trong ngân sách 1.200.000 VNĐ.
* **Action 1**: `search_gifts['Công nghệ', 1200000]`
* **Observation 1**: `Tìm thấy 3 món: Chuột không dây ergonomic - 420,000 VNĐ; Bàn phím cơ mini RGB - 890,000 VNĐ; Tai nghe Bluetooth chống ồn - 1,200,000 VNĐ.`
* **Thought 2**: Kiểm tra tồn kho Tai nghe Bluetooth chống ồn trước khi chốt.
* **Action 2**: `check_gift_stock['Tai nghe Bluetooth chống ồn']`
* **Observation 2**: `'Tai nghe Bluetooth chống ồn': HẾT HÀNG. Hãy gợi ý người dùng chọn món khác cùng nhóm.`
* **Thought 3**: Sản phẩm yêu cầu hết hàng. Kiểm tra phương án thay thế cùng nhóm.
* **Action 3**: `check_gift_stock['Bàn phím cơ mini RGB']`
* **Observation 3**: `'Bàn phím cơ mini RGB' (890,000 VNĐ): CÒN HÀNG - tồn kho 7.`
* **Final Answer**: *"Tai nghe Bluetooth chống ồn hiện HẾT HÀNG — không thể chốt. Đề xuất thay thế: Bàn phím cơ mini RGB, 890.000 VNĐ, CÒN HÀNG (tồn kho 7). Phù hợp cho người mê code, còn dư ~310.000 VNĐ."*
* **Iterations**: 4 | **Tool calls**: `search_gifts`, `check_gift_stock` ×2
* **Nhận xét**: ✅ Agent xử lý xuất sắc tình huống hết hàng — từ chối chốt sản phẩm không khả dụng, tự động tìm phương án thay thế và kiểm tra lại tồn kho. Thể hiện khả năng dynamic decision mạnh.

---

### 📌 TEST CASE #6 — Ngân sách không đủ để mua bất kỳ sản phẩm nào trong nhóm

**Câu hỏi**: *"Tôi muốn mua quà thuộc nhóm Tri thức cho bạn mê đọc sách nhưng ngân sách chỉ có 100.000 VNĐ."*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Với 100.000 VNĐ bạn có thể tặng một cuốn sách đơn lẻ nhỏ hoặc bookmark xinh."*
* **Nhận xét**: Gợi ý mang tính ứng phó, không phản ánh thực tế hệ thống — người dùng có thể kỳ vọng sai về danh mục sản phẩm hiện có.

#### 🧠 ReAct Agent:
* **Thought 1**: Tìm quà nhóm Tri thức trong ngân sách 100.000 VNĐ.
* **Action 1**: `search_gifts['Tri thức', 100000]`
* **Observation 1**: `Không có quà nhóm Tri thức trong ngân sách 100,000 VNĐ. Rẻ nhất là 'Combo sách best-seller' giá 350,000 VNĐ.`
* **Final Answer**: *"Không có quà nhóm Tri thức trong ngân sách 100,000 VNĐ. Rẻ nhất là 'Combo sách best-seller' giá 350,000 VNĐ. Bạn muốn tăng ngân sách đến mức tối thiểu trên hay đổi sang nhóm quà khác?"*
* **Iterations**: 2 | **Tool calls**: `search_gifts['Tri thức', 100000]`
* **Nhận xét**: ✅ Agent trả về kết quả chính xác từ hệ thống, không bịa sản phẩm, chủ động hỏi ngược để dẫn hướng người dùng — xử lý edge case ngân sách thấp đúng cách.

---

### 📌 TEST CASE #7 — Yêu cầu lạc đề, agent phản hồi sai tool

**Câu hỏi**: *"Bạn tôi có sở thích kỳ lạ là sưu tầm thiên thạch và ngắm sao. Hãy phân tích tính cách và chọn quà phù hợp."*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Người thích sưu tầm thiên thạch và ngắm sao thường là người tò mò, sáng tạo và yêu khoa học."*
* **Nhận xét**: Có phân tích tính cách nhưng không gợi ý quà cụ thể, không có dữ liệu thực tế từ hệ thống.

#### 🧠 ReAct Agent:
* **Thought 1**: *(lỗi giao thức — không xử lý được yêu cầu đặc thù)*
* **Action 1**: `search_gifts['Tri thức', 100000]` *(tool call lặp lại từ turn trước — hoàn toàn không liên quan)*
* **Observation 1**: `Không có quà nhóm Tri thức trong ngân sách 100,000 VNĐ. Rẻ nhất là 'Combo sách best-seller' giá 350,000 VNĐ.`
* **Final Answer**: *"Không có quà nhóm Tri thức trong ngân sách 100,000 VNĐ. Rẻ nhất là 'Combo sách best-seller' giá 350,000 VNĐ. Bạn muốn tăng ngân sách đến mức tối thiểu trên hay đổi sang nhóm quà khác?"*
* **Iterations**: 2 | **Tool calls**: `search_gifts['Tri thức', 100000]`
* **Nhận xét**: ❌ Agent thất bại hoàn toàn — gọi tool với tham số từ context của turn trước, không phân tích tính cách, không xử lý yêu cầu thực tế. Đây là lỗi nghiêm trọng: thiếu context isolation giữa các turns và không có cơ chế fallback cho sở thích không có trong danh mục.

---

### 📌 TEST CASE #8 — Input không hợp lệ (ngân sách không phải số)

**Câu hỏi**: *"Hãy tìm cho tôi món quà thuộc nhóm Thể thao với ngân sách 'rất nhiều tiền'."*

#### 🤖 Chatbot Baseline:
* **Phản hồi**: *"Với ngân sách lớn, bạn có thể tặng các thiết bị thể thao cao cấp như đồng hồ thông minh, máy chạy bộ mini, hay bộ dụng cụ yoga cao cấp."*
* **Nhận xét**: Trả lời mơ hồ theo hướng suy đoán, không xác nhận ngân sách cụ thể — có thể gây hiểu lầm về khả năng tư vấn thực tế.

#### 🧠 ReAct Agent:
* **Thought 1**: Gọi tool tìm quà nhóm Thể thao với ngân sách nhập vào.
* **Action 1**: `search_gifts['Thể thao', 'rất nhiều tiền']`
* **Observation 1**: `LỖI: Ngân sách 'rất nhiều tiền' không phải số. Ví dụ: search_gifts['Thể thao', 500000].`
* **Final Answer**: *"Vui lòng nhập ngân sách cụ thể bằng số VNĐ, ví dụ 500000."*
* **Iterations**: 2 | **Tool calls**: `search_gifts['Thể thao', 'rất nhiều tiền']`
* **Nhận xét**: ✅ Agent phát hiện và báo lỗi input validation chính xác, không đoán mò, yêu cầu người dùng nhập lại đúng định dạng — xử lý edge case input sai chuẩn mực.

