"""
🛠️ TOOL REGISTRY (Role 2: Tool & Spec Engineer)
Chủ đề: 🎁 TRỢ LÝ NẮM BẮT TÍNH CÁCH & CHỌN QUÀ TẶNG PHÙ HỢP

Quy tắc: mọi tool đều deterministic, read-only và KHÔNG BAO GIỜ raise Exception
-> lỗi luôn trả về chuỗi bắt đầu bằng "LỖI:" để Agent tự suy luận đổi hướng.
"""

# Từ khóa tính cách ➔ Nhóm gu tặng quà
PERSONALITY_KEYWORDS = {
    "Tri thức": ["đọc sách", "sách", "hướng nội", "trầm tính", "học"],
    "Công nghệ": ["công nghệ", "game", "máy tính", "lập trình", "code"],
    "Thể thao": ["thể thao", "gym", "chạy bộ", "yoga", "hướng ngoại", "năng động"],
}

# Nhóm ➔ [(Tên quà, Giá VNĐ, Tồn kho)]
GIFT_CATALOG = {
    "Tri thức": [
        ("Combo sách best-seller", 350_000, 12),
        ("Đèn đọc sách chống cận", 480_000, 5),
        ("Máy đọc sách Kindle", 2_500_000, 3),
    ],
    "Công nghệ": [
        ("Chuột không dây ergonomic", 420_000, 15),
        ("Bàn phím cơ mini RGB", 890_000, 7),
        ("Tai nghe Bluetooth chống ồn", 1_200_000, 0),  # Bẫy: hết hàng
    ],
    "Thể thao": [
        ("Bình giữ nhiệt 750ml", 280_000, 30),
        ("Thảm tập yoga chống trượt", 390_000, 14),
        ("Vòng đeo tay đo nhịp tim", 950_000, 8),
    ],
}


def analyze_personality(traits: str) -> str:
    """
    Phân tích mô tả tính cách/sở thích để xác định nhóm gu tặng quà.

    Args:
        traits (str): Mô tả tự do, ví dụ 'hướng nội, thích đọc sách'.
    Returns:
        str: Nhóm tính cách nhận diện được, hoặc 'LỖI: ...' kèm danh sách nhóm hợp lệ.
    """
    if not traits or not traits.strip():
        return "LỖI: Thiếu mô tả tính cách. Ví dụ hợp lệ: 'hướng nội, thích đọc sách'."

    text = traits.lower()
    scores = {cat: sum(1 for kw in kws if kw in text)
              for cat, kws in PERSONALITY_KEYWORDS.items()}
    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return (f"LỖI: Không nhận diện được nhóm tính cách từ '{traits}'. "
                f"Các nhóm hợp lệ: {', '.join(PERSONALITY_KEYWORDS)}.")

    return (f"Nhóm tính cách: {best}. "
            f"Gợi ý: gọi search_gifts với category='{best}' kèm ngân sách.")


def search_gifts(category: str, budget_vnd) -> str:
    """
    Tra danh sách quà theo nhóm tính cách, lọc theo ngân sách tối đa.

    Args:
        category (str): Tri thức | Công nghệ | Thể thao
        budget_vnd (int | str): Ngân sách VNĐ, chấp nhận cả '500.000'.
    Returns:
        str: Danh sách quà phù hợp, hoặc 'LỖI: ...' kèm hướng dẫn sửa.
    """
    match = next((c for c in GIFT_CATALOG if c.lower() == str(category).strip().lower()), None)
    if match is None:
        return (f"LỖI: Không tồn tại nhóm quà '{category}'. "
                f"Các nhóm hợp lệ: {', '.join(GIFT_CATALOG)}.")

    try:
        budget = int(str(budget_vnd).replace(".", "").replace(",", "").strip())
    except ValueError:
        return f"LỖI: Ngân sách '{budget_vnd}' không phải số. Ví dụ: search_gifts['{match}', 500000]."

    if budget <= 0:
        return f"LỖI: Ngân sách phải là số dương, nhận được {budget:,} VNĐ."

    found = [(n, p) for n, p, _ in GIFT_CATALOG[match] if p <= budget]
    if not found:
        cheapest = min(GIFT_CATALOG[match], key=lambda g: g[1])
        return (f"Không có quà nhóm {match} trong ngân sách {budget:,} VNĐ. "
                f"Rẻ nhất là '{cheapest[0]}' giá {cheapest[1]:,} VNĐ. Hãy đề nghị nâng ngân sách.")

    lines = [f"Tìm thấy {len(found)} món quà nhóm {match} trong ngân sách {budget:,} VNĐ:"]
    lines += [f"{i}. {n} - {p:,} VNĐ" for i, (n, p) in enumerate(found, 1)]
    lines.append("Gợi ý: gọi check_gift_stock[tên quà] để xác nhận còn hàng.")
    return "\n".join(lines)


def check_gift_stock(gift_name: str) -> str:
    """
    Kiểm tra tồn kho của một món quà cụ thể trước khi chốt.

    Args:
        gift_name (str): Tên món quà (chấp nhận khớp một phần, ví dụ 'Kindle').
    Returns:
        str: Trạng thái CÒN HÀNG / HẾT HÀNG, hoặc 'LỖI: ...' kèm danh sách quà có thật.
    """
    if not gift_name or not gift_name.strip():
        return "LỖI: Thiếu tên quà. Hãy lấy tên từ kết quả của search_gifts."

    query = gift_name.strip().lower()
    for category, gifts in GIFT_CATALOG.items():
        for name, price, stock in gifts:
            if query in name.lower():
                if stock == 0:
                    return (f"'{name}' ({price:,} VNĐ, nhóm {category}): HẾT HÀNG. "
                            "Hãy gợi ý người dùng chọn món khác cùng nhóm.")
                return f"'{name}' ({price:,} VNĐ, nhóm {category}): CÒN HÀNG - tồn kho {stock}."

    all_names = [n for gifts in GIFT_CATALOG.values() for n, _, _ in gifts]
    return f"LỖI: Không có món quà '{gift_name}'. Danh mục hiện có: {'; '.join(all_names)}."


# Danh sách tool đăng ký cho Agent sử dụng
AVAILABLE_TOOLS = {
    "analyze_personality": analyze_personality,
    "search_gifts": search_gifts,
    "check_gift_stock": check_gift_stock,
}


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # (mô tả, hàm, tham số, có kỳ vọng trả LỖI không)
    checks = [
        ("Nhận diện tính cách", analyze_personality, ("hướng nội, mê đọc sách",), False),
        ("Tìm quà theo ngân sách", search_gifts, ("Tri thức", 500_000), False),
        ("Ngân sách dạng chuỗi", search_gifts, ("Công nghệ", "500.000"), False),
        ("Quà còn hàng", check_gift_stock, ("Đèn đọc sách",), False),
        ("Quà hết hàng", check_gift_stock, ("Tai nghe Bluetooth",), False),
        ("Ngân sách quá thấp", search_gifts, ("Tri thức", 10_000), False),
        ("Lỗi: traits rỗng", analyze_personality, ("",), True),
        ("Lỗi: tính cách lạ", analyze_personality, ("thích sưu tầm thiên thạch",), True),
        ("Lỗi: nhóm không tồn tại", search_gifts, ("Ma thuật", 500_000), True),
        ("Lỗi: ngân sách âm", search_gifts, ("Tri thức", -1000), True),
        ("Lỗi: ngân sách không phải số", search_gifts, ("Tri thức", "nhiều tiền"), True),
        ("Lỗi: quà không tồn tại", check_gift_stock, ("Lamborghini",), True),
    ]

    print("=== SELF-TEST TOOLS ===")
    passed = 0
    for desc, func, args, expect_error in checks:
        try:
            out = func(*args)
            ok = out.startswith("LỖI:") == expect_error
        except Exception as e:  # Tool không được phép crash
            out, ok = f"<CRASH> {type(e).__name__}: {e}", False
        passed += ok
        print(f"\n{'✅' if ok else '❌'} {desc} | {args}\n   ➔ {out}")

    print(f"\n=== KẾT QUẢ: {passed}/{len(checks)} PASS | "
          f"{len(AVAILABLE_TOOLS)} tools: {', '.join(AVAILABLE_TOOLS)} ===")
