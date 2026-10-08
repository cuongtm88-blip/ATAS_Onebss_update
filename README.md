# ATS OneBSS

Phiên bản 0.9.37 lưu tuyến dự án của phiếu đang chờ để không mất nhận diện
khi dừng/chạy lại; nếu không đọc được `Tên KH`, app giữ phiếu chưa giao thay vì
áp dụng nhầm quy tắc dịch vụ. Tab `Phiếu chưa giao` hỗ trợ chỉ định nhân sự riêng
cho từng phiếu; chỉ định được lưu qua lần khởi động và chỉ áp dụng cho phiếu đó.

Phiên bản 0.9.36 sửa nhận diện Dự án BTC khi tên đơn vị chỉ có trong `Tên thuê bao`
do ATS chưa đọc được `Tên KH` ở một lượt tải. Phiếu vẫn được định tuyến theo tỉnh
của dự án thay vì rơi xuống quy tắc cân bằng dịch vụ thông thường.

Phiên bản 0.9.35 sửa cập nhật tự động trên macOS: bộ giải nén nay khôi phục
symlink nội bộ cần thiết cho PySide/Qt/Python, giữ quyền thực thi và từ chối
symlink thoát khỏi gói hoặc cấu trúc đường dẫn không an toàn.

Phiên bản 0.9.34 sửa lỗi hàng đợi API dùng số dòng Excel cũ để kiểm tra
quyền gửi. Sau khi thay đổi/chèn dòng trong file Giao phiếu, app đối chiếu
quyền gửi theo tên dịch vụ đã ánh xạ sang Google Sheet, tránh gửi nhầm các
dịch vụ đang tắt `Gửi API`.

Repository này dành riêng cho **Miền Bắc**. Các bản phát hành miền Trung và
miền Nam sẽ dùng repository/cấu hình riêng khi được triển khai; không dùng
chung gói quy tắc hoặc cấu hình dữ liệu giữa các miền.

## Phát hành

Mỗi tag dạng `v*` chạy GitHub Actions để đóng gói ứng dụng cho macOS arm64 và
Windows x64, sau đó đính kèm hai gói vào GitHub Release. File Excel giao phiếu,
quy tắc dự án và `config.toml` được đóng gói công khai theo xác nhận của chủ dự
án. Không đưa hồ sơ Chromium, database SQLite, log, file xem trước, token Telegram
hoặc khóa Supabase vào repository. Khóa Supabase chỉ được cung cấp qua biến môi
trường trên máy chạy chương trình.

Từ phiên bản 0.9.18, giao diện có nút `Kiểm tra phiên bản`. Nút kiểm tra GitHub
Release của miền đang chọn; khi có phiên bản mới, người dùng xác nhận tải và cài.
Gói được xác minh bằng SHA-256 trước khi thay ứng dụng. Chỉ hỗ trợ cập nhật tự
động trên bản đã đóng gói cho macOS arm64 và Windows x64; nếu chưa có Release mới,
ứng dụng sẽ báo không có bản phát hành mới hơn.

Phiên bản 0.9.19 bổ sung phục hồi OneBSS bị treo: tải lại trang trước, rồi khởi động
lại Chromium cùng hồ sơ đăng nhập nếu trang không hồi phục; sau đó mở lại OneBSS,
Google Sheet và tiếp tục chu kỳ tự động.

Phiên bản 0.9.20 bổ sung dự phòng nguồn GitHub Release từ cấu hình đóng gói nếu
`regions.toml` cũ trong Application Support chưa có địa chỉ repository cập nhật.

Phiên bản 0.9.21 bổ sung tùy chọn lưu thông tin đăng nhập OneBSS trong kho bảo
mật của hệ điều hành. Khi phiên hết hạn trong chế độ tự động, ứng dụng thử đăng
nhập lại tối đa 3 lần; OTP được gửi qua Telegram và chỉ nhận từ Chat ID đã cấu
hình. Trong Telegram, gửi mã số 4–8 chữ số hoặc `/otp <mã>`. Nếu hết 3 lần,
worker dừng để tránh khóa tài khoản.

Phiên bản 0.9.24 bổ sung listener Telegram nền trên macOS. Khi Telegram được bật, gửi `/resume` từ chat riêng có Chat ID đã cấu hình để mở lại ATS OneBSS và tiếp tục tự động giao phiếu. Máy Mac cần đang bật, người dùng macOS còn đăng nhập; listener nền hoạt động sau khi đã mở app ít nhất một lần.

Phiên bản 0.9.23 tự động hoàn tất lưu phiên và bắt đầu giao phiếu sau khi đăng nhập OneBSS/Google Sheets thành công.

Phiên bản 0.9.22 áp dụng cùng quy trình đăng nhập tự động và nhận OTP qua Telegram
khi bấm nút `Đăng nhập`; nếu chưa lưu thông tin hoặc chưa bật Telegram, vẫn cho phép
đăng nhập thủ công như trước.

Công cụ tự động đọc các phiếu đang hiển thị trên OneBSS, đối chiếu quy tắc trong
`Giao phiếu_demo2.xlsx`, cân bằng điểm giữa thành viên, giao theo lô, ghi tiếp dữ liệu vào
Google Sheet theo tháng giao phiếu, rồi bấm `Gửi SMS`.

Khi cần đồng bộ song song sang API nhận phiếu, mở tab `API nhận phiếu`, nhập
`X-Ingest-Token` do quản trị API cấp và bật gửi cho miền cần dùng. Token được lưu
trong Keychain macOS hoặc mã hóa bằng Windows DPAPI, không lưu trong file cài đặt
hay nhật ký. Sau khi OneBSS xác nhận giao, ứng dụng gửi các field `Ngày giao`,
`Mã giao dịch`, `Mã thuê bao`, `Dịch vụ`, `Người thực hiện`, `Tỉnh`, `Tên dự án`,
`Tên thuê bao`, `Loại HĐ` và `Địa chỉ lắp đặt`. Không gửi field `Trạng thái`.
Gửi API và ghi Google Sheet độc lập; lỗi API được giữ trong hàng đợi cục bộ để thử
lại ở lần đồng bộ tiếp theo.

Nếu file Excel có cột `Gửi API`, chỉ các dòng dịch vụ có giá trị `Có` (hoặc dấu
`x`) mới được đưa vào hàng đợi gửi API; ô trống/`Không` sẽ không gửi. Nếu workbook
chưa có cột này, ATS giữ hành vi cũ và gửi API cho các dịch vụ như trước. Cột
`Gửi API` không được xem là cột nhân sự và không ảnh hưởng đến quy tắc cân bằng.

Phiên bản 0.9.25 bổ sung đồng bộ phiếu đã giao sang API nhận phiếu, giữ hàng đợi
thử lại độc lập với Google Sheets và lưu API Token trong kho bảo mật hệ điều hành.

Phiên bản 0.9.26 gom các phiếu thường cùng Tên KH, Địa chỉ LĐ và dịch vụ về một
nhân sự; nhóm mới ưu tiên người có tải điểm chuẩn hóa thấp nhất.

Phiên bản 0.9.27 đọc cột `Gửi API` trong file Excel: chỉ dịch vụ đánh dấu `Có`
(hoặc `x`) được gửi sang API; các quy tắc phân phiếu và ghi Google Sheet không đổi.

Phiên bản 0.9.28 ghi thêm dòng cho phiếu đã có trên Google Sheet, giữ người nhận
cũ và đánh dấu `Giao lại` ở cột K để công thức thống kê loại khỏi số phiếu/điểm.
Riêng Voice Brandname, cột K chỉ được đánh dấu khi VIP hiện tại khớp VIP suy ra
từ người nhận cũ trên Sheet (Lê Đức Tuấn = `Giam sat`, người khác = `Xu ly`).
Phiếu VIP `Giam sat` luôn giao Lê Đức Tuấn; VIP `Xu ly` giữ người nhận cũ.

Phiên bản 0.9.29 tự bổ sung cấu hình cột K cho các cấu hình runtime cũ, bỏ chuỗi
nội bộ `Giao lại theo Google Sheet` khỏi dữ liệu dự án/ghi chú, và chỉ đánh dấu
các phiếu Voice Brandname giao lại khi VIP hiện tại khớp trạng thái suy ra từ người
nhận cũ.

Phiên bản 0.9.30 mở website Google Sheets trước OneBSS khi khởi chạy hoặc phục hồi
Chromium, để Google Sheets luôn được mở ở tab đầu tiên.

Phiên bản 0.9.31 gửi thêm `Tên thuê bao`, `Loại HĐ`, `Địa chỉ lắp đặt` qua API
nhận phiếu và không gửi trường `Trạng thái`.

Phiên bản 0.9.32 đối chiếu xác nhận Google Sheet theo thời điểm, mã giao dịch,
mã thuê bao, dịch vụ và người thực hiện; bỏ qua khác biệt định dạng ở các cột
khác để tránh dán lặp phiếu khi Google Sheets biến đổi dấu nháy trong địa chỉ.

Phiên bản 0.9.33 đọc file Giao phiếu dạng Group DV với tỷ lệ nhân sự theo dịch
vụ, ánh xạ dịch vụ OneBSS sang tên Google Sheet/API, và chọn một người nhận duy
nhất theo tỷ lệ tải tháng đã chuẩn hóa theo nhóm nhân sự.

## Nguyên tắc phân bổ

- Với file dạng Group DV, mỗi phiếu được giao cho một người trong các nhân sự có
  tỷ lệ lớn hơn 0 ở dòng quy tắc. App ưu tiên người có tỷ lệ thực hiện tháng thấp
  nhất sau khi chuẩn hóa theo nhóm gốc và hệ số mục tiêu; tỷ lệ % của dịch vụ chỉ
  phân xử khi mức tải tháng bằng nhau. Vì vậy tỷ lệ % là định hướng, không phải
  quota cứng.
- Với workbook kiểu cũ, nếu một phiếu cần người ở nhiều nhóm thì điểm phiếu được
  chia đều cho số người nhận.
- Với cấu hình mặc định `use_backup_members = true`, `Chính` và `Phụ` đều là
  người được phép nhận dịch vụ; không có quota số lượng phiếu theo hai vai trò.
- Tỷ lệ thực hiện của mỗi người là điểm tháng chia cho điểm trung bình của nhóm
  gốc. File Group DV giữ quần thể chuẩn 14 người Nhóm 1 và 4 người Nhóm 2; Lê Đức
  Tuấn vẫn được xét cho dịch vụ của cả hai nhóm nhưng điểm tải tháng của anh ấy
  được so với nhóm gốc Nhóm 2.
- Mức tải chuẩn hóa có tính hệ số cá nhân trong
  `[balance.member_target_ratios]`: Lương Tuấn Thanh, Đào Anh Vũ và Nguyễn Hoàng
  Dương có hệ số 1.10; Đoàn Hải Hà và Nguyễn Duy Thành có hệ số 1.05; tất cả
  nhân sự còn lại mặc định 1.00. App chia điểm tháng cho điểm trung bình nhóm và
  hệ số cá nhân để so tải giữa hai nhóm.
- Các phiếu thường cùng `Tên KH` + `Địa chỉ LĐ` + dịch vụ được gom về một nhân
  sự đủ điều kiện; khi gặp nhóm lần đầu, chọn người có tải điểm chuẩn hóa thấp
  nhất. Thiếu một trong ba trường thì không ghim theo nhóm địa chỉ và phiếu vẫn
  được cân bằng theo quy tắc dịch vụ.
- Phiếu có điểm lớn hoặc ít người đủ điều kiện vẫn được lập kế hoạch trước để giữ
  cân bằng chặt hơn. Các tuyến dự án và Voice Brandname cố định vẫn được áp dụng
  trước cân bằng, nên đôi lúc một cá nhân có thể vượt mức mong muốn.
- Đặt `use_backup_members = false` nếu muốn vô hiệu hóa hoàn toàn người `Phụ`.
- Quy tắc ưu tiên theo khách hàng/dự án nằm trong `project_rules.toml`, được xét
  trước danh sách nhân sự của dịch vụ trong Excel. Mỗi lượt giao lưu cả tên dự án
  vào SQLite để có thể kiểm tra lại.
- `Đài THVN`: nếu `Tên thuê bao` chứa chuỗi này, giao cho Nguyễn Hoàng Dương.
- `Đài Phát Thanh - Truyền Hình Hà Nội`: nếu `Tên KH` chứa
  `ĐÀI PHÁT THANH - TRUYỀN HÌNH HÀ NỘI`, giao cho Nguyễn Hoàng Dương.
- `Cục Quản Trị NHNN - Kênh phục vụ HNTH`: khi `Tên KH` chứa `Cục Quản Trị
  Ngân Hàng Nhà Nước Việt Nam` và `Ghi chú` chứa `Kênh phục vụ HNTH`, giao cho
  Nguyễn Hoàng Dương.
- `Dự án BCA`: nhận diện bằng `Tên KH`/`Tên thuê bao` chứa
  `Cục Viễn Thông & Cơ Yếu Bca`, sau đó định tuyến theo `Địa chỉ LĐ`. Nếu địa chỉ
  không khớp tỉnh/thành đã khai báo, phiếu được đưa vào `preview_skipped.csv` thay
  vì giao theo quy tắc dịch vụ thông thường.
- `Dự án BHXH`: nhận diện khi `Tên KH` chứa `Ban Quản Lý Đầu Tư Và Xây Dựng
  Ngành Bảo Hiểm Xã Hội`; dùng cùng bảng định tuyến tỉnh/thành như Dự án BCA.
- `Dự án Cục BĐTW`: nhận diện khi `Tên KH` chứa `Cục BĐTW` và dùng cùng bảng
  định tuyến tỉnh/thành theo `Địa chỉ LĐ` như Dự án BCA.
- `Dự án BTC`: nhận diện khi `Tên KH` chứa `Tổng Cục Dự Trữ Nhà Nước`,
  `Tổng Cục Thuế`, `Cục Công Nghệ Thông Tin & Thống Kê Hải Quan` hoặc
  `Kho Bạc Nhà Nước`; dùng cùng bảng định tuyến tỉnh/thành như Dự án BCA.
- `Dự án Vietlott`: nhận diện khi `Tên KH` chứa
  `Công Ty Cổ Phần Đầu Tư Kỹ Thuật Berjaya Gia Thịnh`. Tuyến cố định gồm Vũ
  Thế Ninh (Hưng Yên, Thái Bình, Điện Biên, Hải Phòng, Hải Dương, Lào Cai,
  Yên Bái, Hà Tĩnh, Hà Giang); Đào Anh Vũ (Thái Nguyên, Bắc Kạn, Lai Châu,
  Ninh Bình, Nam Định, Hà Nam, Hà Nội, hoặc Tuyên Quang với mã thuê bao
  `BGT5…`); Đoàn Hải Hà (Sơn La, Cao Bằng, Phú Thọ, Vĩnh Phúc, Hòa Bình,
  Lạng Sơn); Nguyễn Duy Thành (Bắc Ninh, Bắc Giang, Nghệ An, Quảng Ninh,
  Thanh Hóa, hoặc Tuyên Quang với mã thuê bao `BGT2…`). Lê Đức Vinh không còn
  được phân công cho dự án nào.
- Trong các dự án dùng chung bảng tuyến BCA/BTC/Cục BĐTW, Nghệ An giao Nguyễn
  Duy Thành và Hà Tĩnh giao Vũ Thế Ninh.
- Mọi dự án xác định tỉnh/thành từ `Địa chỉ LĐ` trước. Chỉ khi trường này không
  khớp địa bàn đã khai báo, công cụ mới dùng cột `Tỉnh LĐ`. `Địa chỉ KN` không
  tham gia định tuyến dự án.
- Việc cân bằng luôn tuân thủ tập nhân sự được phép nhận từng dịch vụ và các tuyến
  dự án cố định. Vì vậy điểm lịch sử không thể bằng tuyệt đối nếu hai nhóm kỹ năng
  không có dịch vụ chung; công cụ sẽ ưu tiên người thấp điểm nhất trong tập hợp lệ
  để thu hẹp chênh lệch ở các lượt tiếp theo.
- Voice Brandname áp dụng trực tiếp quy tắc theo cột `VIP xử lý`.
  `Giam sat` luôn giao Lê Đức Tuấn, kể cả phiếu giao lại đang ghi người khác trên
  Google Sheet. `Xu ly` giao cân bằng giữa Đỗ Thị Thu Trang, Ngô Thùy Trang và
  Nguyễn Thị Thu Trang; nếu là phiếu giao lại thì giữ người nhận ban đầu trên
  Google Sheet. Với phiếu giao lại, người nhận cũ Lê Đức Tuấn được xem là `Giam sat`,
  người khác là `Xu ly`; cột K chỉ ghi `Giao lại` nếu trạng thái hiện tại giống
  trạng thái suy ra từ người nhận cũ. VIP trống/không nhận diện được sẽ bị bỏ qua.
- Tên miền áp dụng điểm theo `Loại HĐ`; MegaWan/MetroNet áp dụng theo `Loại kênh`.
- `Thoại quốc tế` vẫn được giao cho Trần Mạnh Cường theo file Excel, nhưng có
  điểm bằng 0 và không được ghi vào Google Sheet. Dòng lịch sử trên Sheet không
  được dùng để thay đổi người nhận của dịch vụ đặc biệt này.
- SQLite `ats_onebss.db` lưu điểm và trạng thái từng bước để chống giao hoặc ghi trùng.
- Trước khi phân công, công cụ đọc các tab tháng trên Google Sheet và đối chiếu đồng
  thời `Mã giao dịch` + `Mã thuê bao`. Người thực hiện gần nhất trên Sheet được ưu
  tiên tuyệt đối khi giao lại. Nếu cặp mã đã có trong tab của tháng hiện tại, phiếu
  không được cộng điểm hoặc nối thêm dòng; nếu chỉ có ở tab tháng khác, phiếu được
  coi là lượt mới của tháng hiện tại, vẫn giao đúng người cũ và được ghi thêm dòng.
  Vì vậy Google Sheet là dữ liệu chuẩn; nếu giao lại thủ công, hãy sửa người thực
  hiện tại dòng tương ứng trên Sheet trước chu kỳ kế tiếp.
- Tổng `Điểm quy đổi` của tháng cũng được đọc từ tab tháng hiện tại để việc cân bằng
  phản ánh cả các chỉnh sửa thủ công trên Google Sheet, thay vì chỉ dựa vào SQLite
  của riêng máy chạy chương trình.
- Dịch vụ không khớp quy tắc sẽ được bỏ qua an toàn và ghi vào
  `preview_skipped.csv`; công cụ không tự đoán người nhận. Cuối mỗi chu kỳ, Terminal
  báo riêng số phiếu đã giao và số phiếu vẫn chưa giao vì thiếu quy tắc.

## Cài đặt

```bash
cd /Users/tranmanhcuong/Projects/ATS_OneBss
uv sync --extra dev
uv run playwright install chromium
```

Đăng nhập OneBSS và Google lần đầu trong Chromium riêng:

```bash
uv run ats-onebss login
```

Chương trình dùng bản Google Chrome chính thức trên máy (không dùng Chrome for
Testing). Phiên đăng nhập chỉ được lưu cục bộ trong `.browser-profile`; không lưu mật
khẩu vào mã nguồn hay file cấu hình.

## Chạy thử và chạy thật

### Giao diện macOS

Mở giao diện khi chạy từ mã nguồn:

```bash
uv run ats-onebss-gui
```

Trong giao diện:

1. Chọn miền hoạt động. Hiện `Miền Bắc` đã được bật với toàn bộ cấu hình hiện
   tại; `Miền Trung` và `Miền Nam` được hiển thị nhưng khóa chạy cho đến khi có
   file nhân sự và quy tắc riêng.
2. Chọn nhân sự tạm nghỉ nếu có. Danh sách này được lưu riêng cho từng miền.
3. Bấm `Đăng nhập` ở lần đầu, đăng nhập OneBSS và Google rồi bấm
   `Hoàn tất đăng nhập`.
4. Bấm `Chạy thử` để tạo CSV xem trước hoặc `Bắt đầu tự động` để chạy liên tục.
   Có thể đổi ô `Chu kỳ` trên giao diện trước khi bấm chạy; mặc định là 15 phút
   và lựa chọn được lưu riêng cho từng miền.
5. Bấm `Dừng` để gửi yêu cầu dừng an toàn. Khi tùy chọn giữ máy được bật, giao
   diện tự chạy `caffeinate -ims` trong đúng thời gian tiến trình hoạt động.
6. Tab `Nhật ký hoạt động` hiển thị nguyên luồng đầu ra theo thời gian thực giống
   Terminal; bản đầy đủ cũng được lưu tại
   `~/Library/Application Support/ATS-OneBSS/logs/<mien>.log`.
7. Tab `Phiếu chưa giao` tự đọc lại `preview_skipped.csv` sau mỗi 2 giây và hiển
   thị đầy đủ mã phiếu, thông tin nhận diện cùng lý do không khớp quy tắc.
8. Trong tab `Telegram`, nhập Bot Token và Chat ID, bật thông báo rồi bấm
   `Gửi tin thử`. Bot Token được lưu trong Keychain macOS; file cài đặt giao diện
   chỉ lưu Chat ID và trạng thái bật/tắt. Khi chu kỳ lỗi hoặc tiến trình dừng bất
   thường, chương trình gửi cảnh báo và vẫn tiếp tục cơ chế phục hồi hiện có.

Để tạo Bot Token, dùng `@BotFather` trên Telegram. Chat ID có thể là ID người dùng,
nhóm hoặc kênh mà bot đã được thêm và có quyền gửi tin. Cấu hình Telegram được lưu
riêng theo từng miền và có hiệu lực từ lần bắt đầu tiến trình tiếp theo.

Danh mục miền nằm trong `regions.toml`. Lệnh Terminal cũ vẫn hoạt động; có thể
chọn rõ Miền Bắc bằng:

```bash
uv run ats-onebss --region north run --yes
```

Miền Trung và Miền Nam dùng các thư mục cấu hình riêng được khai báo sẵn trong
`regions.toml`, không dùng chung browser profile, SQLite, Google Sheet hoặc file
quy tắc với Miền Bắc khi được kích hoạt sau này.

Luôn tạo bản xem trước trước:

```bash
uv run ats-onebss plan
```

Kiểm tra `preview_assignments.csv`, sau đó mới chạy thật:

`run` chạy liên tục, xử lý các phiếu đang hiển thị trong bảng OneBSS rồi tự kiểm tra
lại sau mỗi 15 phút:

```bash
uv run ats-onebss run --yes
```

Chromium được giữ mở trong suốt phiên. Nhấn `Ctrl+C` tại Terminal để dừng an toàn.
Đổi chu kỳ bằng `poll_interval_minutes` trong `config.toml`.
Chu kỳ thành công mới chờ đủ thời gian này. Nếu chu kỳ gặp lỗi, chương trình đồng
bộ phần Google Sheet còn dang dở, tải lại danh sách OneBSS và thử lại sau
`error_retry_seconds` (mặc định 3 giây).

Nếu chỉ muốn xử lý hàng đợi hiện tại rồi đóng Chromium:

```bash
uv run ats-onebss once --yes
```

### Tạm loại nhân sự nghỉ phép

Thêm `--exclude` sau lệnh chạy để không giao bất kỳ phiếu nào cho nhân sự nghỉ,
kể cả dự án, tuyến cố định và phiếu giao lại theo Google Sheet:

```bash
uv run ats-onebss run --yes --exclude "Lê Đức Vinh"
```

Có thể dùng nhiều lần khi nhiều người cùng nghỉ:

```bash
uv run ats-onebss run --yes \
  --exclude "Lê Đức Vinh" \
  --exclude "Nguyễn Duy Thành"
```

Tên được kiểm tra theo danh sách nhân sự trong Excel. Nếu dự án hoặc phiếu giao
lại chỉ có một người hợp lệ và người đó đang nghỉ, phiếu được giữ chưa giao và lý
do được ghi vào `preview_skipped.csv`; chương trình không tự ý đổi người phụ trách.
Khi nhân sự đi làm lại, nhấn `Ctrl+C` rồi chạy lại lệnh bình thường, không thêm
`--exclude`.

Sau khi `Ghi lại`, công cụ bấm `Gửi SMS` và chấp nhận hộp xác nhận nếu website hiển
thị. Công cụ không chờ hoặc khẳng định trạng thái SMS đã gửi thành công.

## Google Sheet

Công cụ dùng chính phiên Google đã đăng nhập trong Chromium và thao tác giao diện
Google Sheets; không cần Google Sheets API. Tab được chọn theo thời điểm giao, ví dụ
`Tháng 8/2026` hoặc `Tháng 9/2026`, theo mẫu `sheet_name_template`. Tab tương ứng cần
tồn tại sẵn trong file Google Sheet. Công cụ tìm dòng cuối và nối dữ liệu từ cột A
đến K theo thứ tự:

1. Ngày giao
2. Mã giao dịch
3. Mã thuê bao
4. Dịch vụ
5. Người được giao
6. Tên thuê bao
7. Loại HĐ
8. Địa chỉ lắp đặt
9. Tỉnh lắp đặt
10. Tên dự án (để trống nếu không phải phiếu dự án)
11. Ghi `Giao lại` nếu phiếu đã có trên Google Sheet và thuộc lượt giao lại được
    đánh dấu; trường hợp Voice Brandname còn phụ thuộc VIP hiện tại so với người cũ.

Các cột từ B đến K được khai báo bằng `columns` trong `config.toml`; cột A là thời
điểm giao do chương trình tự thêm. Có thể thêm `"points"` vào cuối danh sách nếu
cần ghi cả điểm của từng người.

Tên thuê bao và Tỉnh LĐ được đọc từ bảng/API OneBSS trước. Nếu Tên thuê bao còn
thiếu, chương trình đọc trường `Tên TB` trong phần chi tiết của đúng phiếu. Nếu
Tỉnh LĐ trống nhưng Địa chỉ LĐ có ghi rõ tỉnh/thành phố, chương trình dùng địa danh
đó; không thay thế bằng Tỉnh quản lý HĐ.

Phiếu đã có trong bất kỳ tab tháng nào vẫn được giao và ghi thành dòng mới trong tab
tháng hiện tại. Phiếu giao lại thông thường giữ người nhận cũ và đánh dấu `Giao lại`
ở cột K. Với Voice Brandname, VIP `Xu ly` giữ người cũ; VIP `Giam sat` giao Lê Đức
Tuấn. Chỉ đánh dấu K khi VIP hiện tại trùng trạng thái suy ra từ người nhận cũ; dù
không đánh dấu K, phiếu vẫn được xem là đã có và không làm tăng điểm. Trong
`preview_assignments.csv`, cột `Trạng thái Google Sheet` nêu rõ có đánh dấu hay không.
Các quy tắc Excel chứa cụm `không đưa vào danh
sách` được chặn ở cả bước lập kế hoạch, hàng đợi SQLite và bước ghi Sheet.

## Kết nối phân hệ Giao phiếu trên Dashboard

Kết nối Dashboard hiện được tạm tắt bằng `enabled = false` trong phần
`[dashboard]` của `config.toml`. ATS-OneBSS chỉ giao phiếu và ghi Google Sheet;
mọi biến API key còn tồn tại trong Terminal đều được bỏ qua.

Website `dashboard.cnttdvs.info` tạo lệnh giao lại qua hàng đợi Supabase; chỉ máy
đang chạy ATS-OneBSS mới thao tác OneBSS. Cấu hình `[dashboard]` trong
`config.toml` chứa URL công khai và tên agent. Khóa `service_role` chỉ đặt ở biến
môi trường cục bộ, không ghi vào file:

```bash
export ATS_ONEBSS_SUPABASE_SERVICE_KEY='DAN_KHOA_SERVICE_ROLE_VAO_DAY'
uv run ats-onebss run --yes
```

Phải thay phần `DAN_KHOA_SERVICE_ROLE_VAO_DAY` bằng khóa thật lấy từ Supabase;
không chạy nguyên văn nội dung minh họa. Nếu chỉ cần phục hồi các dòng đã giao
nhưng còn thiếu trên Google Sheet/Dashboard mà không giao thêm phiếu OneBSS:

```bash
uv run ats-onebss sync --yes
```

Agent đồng bộ các phiếu đã giao lên đồng thời Google Sheet và Dashboard. Cứ 5 giây
agent kiểm tra lệnh giao lại. Với lệnh hợp lệ, agent chuyển bộ lọc OneBSS sang
`Tất cả`, tìm đúng Mã giao dịch/Mã thuê bao, xóa đúng người cũ, chọn người mới với
nhiệm vụ `Kiểm tra và xử lý`, rồi sửa người thực hiện tại đúng dòng Google Sheet.
Điểm tháng trong SQLite được chuyển từ người cũ sang người mới để các lượt cân bằng
tiếp theo dùng đúng số liệu hiện tại.

Quy tắc dự án trong `project_rules.toml` hiện gồm `Đài THVN`, `Cục Quản Trị NHNN - Kênh phục vụ HNTH`, `Dự án BCA`,
`Dự án BHXH`, `Dự án Cục BĐTW`, `Dự án BTC` và `Dự án Vietlott`. Các dự án được nhận diện
theo `Tên KH`; định tuyến theo `Địa chỉ LĐ` và dự phòng bằng `Tỉnh LĐ` khi địa
chỉ chính không xác định được tỉnh/thành. Riêng Vietlott tách Tuyên Quang theo
tiền tố mã thuê bao `BGT2` và `BGT5`.

## Kiểm thử

```bash
uv run pytest
```

Lần đầu nên chạy trên 3–10 phiếu. Sau khi xác nhận selector OneBSS và vị trí cột
Google Sheet vẫn đúng, mới tăng `batch_size`.

## Đóng gói để chạy trên máy khác

Ứng dụng được đóng gói riêng cho từng hệ điều hành. Trên macOS:

```bash
./scripts/build_app.sh
```

Kết quả macOS là `dist/ATS-OneBSS.app`; có thể mở trực tiếp bằng Finder, không
cần Terminal. Ở lần mở đầu tiên, ứng dụng sao chép cấu hình Miền Bắc vào
`~/Library/Application Support/ATS-OneBSS` và tạo browser profile mới tại đó.
Ứng dụng đóng gói không dùng hoặc ghi đè browser profile trong thư mục mã nguồn.

Trên Windows, chạy PowerShell:

```powershell
.\scripts\build_app.ps1
```

Kết quả nằm trong `dist/ATS-OneBSS`. Khi chuyển máy, sao chép cả thư mục này cùng
  `config.toml`, `project_rules.toml` và `Giao phiếu_demo2.xlsx`. Máy đích cần cài Google Chrome, sau đó chạy
`ATS-OneBSS login` một lần để đăng nhập OneBSS và Google. Không sao chép
`.browser-profile` vì phiên đăng nhập thuộc riêng từng máy/người dùng.
