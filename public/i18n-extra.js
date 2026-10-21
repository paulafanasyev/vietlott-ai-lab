// Bản dịch tiếng Nga bổ sung (tải trước giao diện chính). Thêm phương pháp/giả thuyết mới ở đây.
window.RU_EXTRA = {
  "Chuỗi Markov (lặp lại kỳ trước)": "Цепь Маркова (повтор с прошлого тиража)",
  "Ước lượng xác suất mỗi số về kỳ tới tùy theo nó có về ở kỳ vừa quay hay không (chuỗi Markov 2 trạng thái).": "Оценивает шанс каждого числа в зависимости от того, выпало ли оно в прошлом тираже (цепь Маркова с 2 состояниями).",
  "Lấy mẫu Bayes (Thompson)": "Байесовская выборка (Томпсон)",
  "Mỗi số có phân phối hậu nghiệm Gamma theo số lần đã về; rút ngẫu nhiên từ hậu nghiệm và chọn 6 số cao nhất.": "Для каждого числа строится апостериорное Гамма-распределение по числу выпадений; из него берётся случайная выборка и 6 лучших чисел.",
  "Chu kỳ riêng của từng số": "Собственный цикл каждого числа",
  "Với khoảng cách hiện tại từ lần về cuối, tính tỷ lệ «về ngay ở khoảng cách này» theo lịch sử riêng của từng số.": "По текущей паузе с последнего выпадения считает, как часто именно это число возвращалось после такой паузы.",
  "Bỏ phiếu tự học (M1–M15)": "Самообучающееся голосование (M1–M15)",
  "Mỗi phương pháp bỏ phiếu cho 6 số của mình; phiếu có trọng số tự cập nhật sau mỗi kỳ theo thành tích gần đây (Hedge).": "Каждый метод голосует за свои 6 чисел; вес голоса сам обновляется после каждого тиража по недавним результатам (Hedge)."
};
// Tiêu đề giả thuyết có dạng «[Mega 6/45] …»: mẫu khớp phần cuối, giữ nguyên tiền tố trò chơi.
window.RU_PATTERNS = [
  ["Số nóng toàn bộ lịch sử$", "Горячие числа за всю историю"],
  ["Số lâu chưa về$", "Давно не выпадавшие"],
  ["Số hay đi cùng các số của kỳ vừa quay$", "Числа, часто выпадающие вместе с числами прошлого тиража"],
  ["Lặp lại các số của kỳ vừa quay$", "Повтор чисел прошлого тиража"],
  ["Số liền kề \\(±1\\) các số của kỳ vừa quay$", "Соседи (±1) чисел прошлого тиража"],
  ["Số nóng trong (\\d+) kỳ gần nhất$", "Горячие за $1 тиражей"],
  ["Số lạnh trong (\\d+) kỳ gần nhất$", "Холодные за $1 тиражей"],
  ["Tần suất giảm dần, chu kỳ bán rã (\\d+) kỳ$", "Частота с затуханием, полураспад $1 тиражей"],
  ["Số nóng (\\d+) kỳ, cân bằng 3 chẵn / 3 lẻ$", "Горячие за $1 тиражей, 3 чётных / 3 нечётных"],
  ["Số nóng (\\d+) kỳ, cân bằng 3 nhỏ / 3 lớn$", "Горячие за $1 тиражей, 3 малых / 3 больших"],
  ["Số nóng (\\d+) kỳ, bỏ các số của kỳ vừa quay$", "Горячие за $1 тиражей без чисел прошлого тиража"],
  ["Kết hợp số nóng (\\d+) kỳ với tần suất giảm dần \\(bán rã (\\d+) kỳ\\)$", "Горячие за $1 тиражей + затухание (полураспад $2)"],
  ["Số nóng (\\d+) kỳ, mỗi vùng số một số$", "Горячие за $1 тиражей, по одному из каждой зоны"]
];
