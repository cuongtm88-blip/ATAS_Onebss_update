# AGENTS.md

## 1. Mục tiêu

File này quy định cách AI coding agent làm việc trong repository này.

Mục tiêu ưu tiên theo thứ tự:

1.  Hiểu đúng yêu cầu trước khi sửa code.
2.  Thực hiện thay đổi nhỏ nhất có thể để đáp ứng yêu cầu.
3.  Giữ ổn định các chức năng đang hoạt động.
4.  Tìm và sửa nguyên nhân gốc của lỗi thay vì workaround.
5.  Hạn chế đọc, sửa hoặc refactor những phần không liên quan.
6.  Tự kiểm tra thay đổi trước khi kết thúc.
7.  Sử dụng context và thời gian xử lý hiệu quả.

------------------------------------------------------------------------

## 2. Nguyên tắc chung

-   Luôn đọc yêu cầu của người dùng và xác định phạm vi trước khi chỉnh
    sửa.
-   Ưu tiên giải pháp đơn giản, rõ ràng và dễ bảo trì.
-   Giữ nguyên kiến trúc, coding style và conventions hiện tại của
    project.
-   Không tự ý refactor chỉ vì code có thể viết đẹp hơn.
-   Không đổi tên file, function, variable, API, route, database field
    hoặc component nếu không cần cho yêu cầu.
-   Không thay đổi hành vi hiện tại ngoài phạm vi được yêu cầu.
-   Không thêm dependency/package mới nếu giải pháp hiện tại có thể thực
    hiện bằng dependency đã có.
-   Không nâng version package/framework nếu không được yêu cầu.
-   Không sửa formatting hàng loạt hoặc tạo diff không cần thiết.
-   Không xóa code, comment, test hoặc configuration chưa xác định là
    không còn cần thiết.
-   Không thay đổi secrets, credentials, `.env` hoặc dữ liệu production.
-   Không commit, push, deploy hoặc thực hiện thao tác phá hủy dữ liệu
    trừ khi người dùng yêu cầu rõ ràng.
-   Nếu phát hiện vấn đề khác ngoài phạm vi, chỉ báo cáo; không tự sửa.

------------------------------------------------------------------------

## 3. Trước khi sửa code

Trước mỗi task:

1.  Xác định chính xác yêu cầu và kết quả mong muốn.
2.  Tìm entry point hoặc implementation liên quan.
3.  Chỉ đọc những file cần thiết để hiểu luồng xử lý.
4.  Theo dependency/call chain khi thực sự cần.
5.  Xác định phạm vi file dự kiến phải thay đổi.
6.  Với bug, hình thành giả thuyết về root cause trước khi sửa.

Không scan hoặc phân tích toàn bộ repository nếu task chỉ liên quan một
khu vực nhỏ.

Không mở nhiều file chỉ để "hiểu project" khi đã đủ thông tin để thực
hiện task an toàn.

Nếu yêu cầu có nhiều cách hiểu và lựa chọn đó có thể gây thay đổi đáng
kể, hãy hỏi người dùng trước. Với chi tiết nhỏ có thể suy ra an toàn từ
code hiện tại, ưu tiên convention hiện có.

------------------------------------------------------------------------

## 4. Khi sửa bug

Quy trình mặc định:

1.  Reproduce hoặc xác định điều kiện gây lỗi nếu có thể.
2.  Đọc code trực tiếp liên quan.
3.  Kiểm tra log/error/stack trace/test hiện có.
4.  Xác định root cause.
5.  Thực hiện fix nhỏ nhất giải quyết root cause.
6.  Kiểm tra lại trường hợp gây lỗi.
7.  Kiểm tra regression ở các luồng trực tiếp liên quan.

Không sửa thử nhiều nơi khi chưa có bằng chứng.

Nếu lần sửa đầu không giải quyết được lỗi:

-   Dừng việc mở rộng thay đổi.
-   Kiểm tra lại giả thuyết ban đầu.
-   Xem diff hiện tại, log và kết quả test.
-   Xác định vì sao giải pháp trước thất bại.
-   Chỉ tiếp tục sửa khi có giả thuyết tốt hơn.

Ưu tiên sửa nguyên nhân gốc thay vì thêm điều kiện đặc biệt để che lỗi.

------------------------------------------------------------------------

## 5. Khi thêm hoặc nâng cấp chức năng

Trước khi implement:

-   Tìm component/service/function/pattern tương tự đang có.
-   Tận dụng abstraction và conventions hiện tại.
-   Xác định frontend, backend, database và API nào thực sự cần thay
    đổi.

Khi implement:

-   Chỉ thêm code cần thiết.
-   Giữ backward compatibility khi có thể.
-   Không thay đổi API contract nếu không cần.
-   Không tạo abstraction mới cho một trường hợp đơn giản nếu chưa có
    nhu cầu tái sử dụng rõ ràng.
-   Tránh duplicate logic nhưng không over-engineer.
-   Giữ UI/UX hiện tại nếu yêu cầu không nói thay đổi giao diện.

------------------------------------------------------------------------

## 6. Frontend / UI

Khi chỉnh frontend:

-   Tận dụng component và design pattern hiện có.
-   Giữ typography, spacing, responsive behavior và interaction nhất
    quán.
-   Không thay đổi layout ngoài khu vực được yêu cầu.
-   Không thêm thư viện UI mới nếu component hiện có đủ dùng.
-   Không thay đổi state management architecture chỉ để thực hiện
    feature nhỏ.
-   Kiểm tra loading, empty, error và disabled states nếu chúng liên
    quan trực tiếp.
-   Giữ accessibility hiện có; với control mới, dùng semantic element và
    label phù hợp.
-   Không hard-code dữ liệu nếu project đã có nguồn dữ liệu/config tương
    ứng.

Với thay đổi CSS nhỏ, chỉ sửa selector/component liên quan và tránh
formatting lại toàn bộ stylesheet.

------------------------------------------------------------------------

## 7. Backend / API

Khi chỉnh backend:

-   Giữ API contract hiện tại trừ khi yêu cầu buộc phải thay đổi.
-   Validate input ở boundary phù hợp.
-   Xử lý lỗi theo pattern hiện có.
-   Không expose stack trace, secret hoặc dữ liệu nhạy cảm ra client.
-   Không tạo query/database call lặp lại không cần thiết.
-   Tránh thay đổi business logic ngoài phạm vi.
-   Với endpoint mới, ưu tiên conventions của endpoint hiện có.
-   Nếu thay đổi response schema, kiểm tra các consumer trực tiếp liên
    quan.

------------------------------------------------------------------------

## 8. Database

-   Không xóa bảng/cột/dữ liệu nếu không có yêu cầu rõ ràng.
-   Không chạy destructive migration trên dữ liệu thật.
-   Migration phải nhỏ, có mục đích rõ ràng và tương thích với code.
-   Kiểm tra uniqueness, nullability, foreign key và index khi chúng
    liên quan đến yêu cầu.
-   Dùng transaction cho thao tác nhiều bước khi project hiện tại hỗ
    trợ/phù hợp.
-   Tránh N+1 query và query toàn bộ dữ liệu khi chỉ cần một phần.
-   Không thay schema chỉ để né một lỗi ở application layer nếu schema
    hiện tại đúng.

------------------------------------------------------------------------

## 9. Import / Export dữ liệu

Với Excel, CSV hoặc dữ liệu bulk:

-   Không giả định dữ liệu đầu vào luôn sạch.
-   Validate header/field bắt buộc.
-   Xử lý giá trị rỗng và kiểu dữ liệu không hợp lệ.
-   Giữ encoding và định dạng ngày giờ nhất quán.
-   Khi update dữ liệu, xác định khóa nhận diện rõ ràng trước khi ghi.
-   Tránh tạo duplicate khi yêu cầu là upsert/update.
-   Không ghi đè dữ liệu hợp lệ bằng giá trị rỗng nếu nghiệp vụ không
    yêu cầu.
-   Với lượng dữ liệu lớn, ưu tiên batch operation thay vì query/write
    từng dòng nếu stack hiện tại hỗ trợ.

------------------------------------------------------------------------

## 10. Security

Mọi thay đổi phải tránh tạo lỗ hổng mới.

Đặc biệt kiểm tra khi task liên quan:

-   Authentication.
-   Authorization.
-   File upload.
-   SQL/database query.
-   User-generated HTML.
-   URL/redirect.
-   API key/token.
-   CORS.
-   Cookie/session.
-   Command execution.

Nguyên tắc:

-   Không hard-code password, API key hoặc token.
-   Không log secrets.
-   Không tin dữ liệu từ client.
-   Dùng parameterized query/ORM thay vì nối chuỗi SQL.
-   Escape/sanitize output theo framework hiện tại khi cần.
-   Kiểm tra quyền ở server, không chỉ ẩn nút trên frontend.
-   Không vô hiệu hóa security control chỉ để làm chức năng hoạt động.

Nếu phát hiện lỗ hổng nghiêm trọng trong vùng code đang sửa, báo rõ cho
người dùng.

------------------------------------------------------------------------

## 11. Dependency

Trước khi thêm package:

1.  Kiểm tra project đã có package/function tương đương chưa.
2.  Kiểm tra standard library/framework có giải quyết được không.
3.  Chỉ thêm dependency khi lợi ích rõ ràng.

Không chạy upgrade toàn bộ dependencies để giải quyết một task không
liên quan.

Nếu phải thêm dependency, giải thích ngắn gọn lý do trong báo cáo cuối.

------------------------------------------------------------------------

## 12. Testing và kiểm tra

Sau khi sửa, chạy kiểm tra phù hợp với phạm vi thay đổi nếu project có
hỗ trợ.

Ưu tiên theo thứ tự:

1.  Test trực tiếp liên quan.
2.  Type check.
3.  Lint.
4.  Unit/integration test liên quan.
5.  Build.
6.  Test suite rộng hơn khi thay đổi có phạm vi lớn.

Không chạy test/build rất tốn thời gian nếu thay đổi nhỏ và đã có kiểm
tra mục tiêu phù hợp, trừ khi project yêu cầu.

Không sửa test chỉ để làm test pass nếu test đang phản ánh behavior
đúng.

Nếu không thể chạy test:

-   Không nói rằng thay đổi đã được kiểm chứng.
-   Nêu rõ kiểm tra nào chưa chạy và lý do.

Không che giấu warning/error phát sinh từ thay đổi mới.

------------------------------------------------------------------------

## 13. Hiệu quả sử dụng context

Để tránh tiêu tốn context không cần thiết:

-   Bắt đầu từ file/function gần yêu cầu nhất.
-   Dùng search theo symbol, route, error message hoặc keyword cụ thể.
-   Chỉ mở phần file cần thiết trước; mở rộng khi thiếu context.
-   Không đọc generated files, build artifacts hoặc dependencies nếu
    không cần.
-   Tránh các thư mục như `node_modules`, `.git`, `dist`, `build`,
    cache, logs lớn và generated output trừ khi task yêu cầu.
-   Không đọc lại file không thay đổi nếu context hiện tại đã đủ.
-   Không lặp lại phân tích dài khi chỉ cần thực hiện bước tiếp theo.

------------------------------------------------------------------------

## 14. Giới hạn phạm vi thay đổi

Trước khi kết thúc, kiểm tra diff và tự hỏi:

-   File này có thực sự cần sửa không?
-   Dòng này có liên quan trực tiếp đến yêu cầu không?
-   Có accidental formatting changes không?
-   Có behavior ngoài phạm vi bị thay đổi không?
-   Có dependency/config/schema nào bị thay đổi không cần thiết không?

Nếu có, thu hẹp diff trước khi hoàn thành.

------------------------------------------------------------------------

## 15. Git

Nếu repository dùng Git:

-   Kiểm tra trạng thái hiện tại trước khi thực hiện thay đổi lớn.
-   Không ghi đè hoặc revert thay đổi của người dùng không liên quan đến
    task.
-   Không dùng destructive command như `reset --hard`, `clean -fd` hoặc
    force push nếu không được yêu cầu rõ ràng.
-   Không tự commit hoặc push nếu người dùng chỉ yêu cầu sửa code.
-   Diff cuối cùng phải tập trung vào task hiện tại.

------------------------------------------------------------------------

## 16. Khi nào cần hỏi người dùng

Hỏi trước khi tiếp tục nếu:

-   Yêu cầu có hai cách hiểu dẫn đến behavior khác nhau đáng kể.
-   Cần xóa hoặc migrate dữ liệu.
-   Cần thay đổi public API/API contract.
-   Cần thêm dịch vụ trả phí hoặc dependency lớn.
-   Cần thay đổi authentication/authorization architecture.
-   Cần secret/credential chưa có.
-   Có nguy cơ làm mất dữ liệu.
-   Yêu cầu mâu thuẫn với behavior hiện tại và không thể suy ra lựa chọn
    đúng.

Không hỏi những câu có thể trả lời an toàn bằng cách đọc code hiện tại.

------------------------------------------------------------------------

## 17. Khi gặp vấn đề ngoài phạm vi

Nếu phát hiện:

-   Bug khác.
-   Code smell.
-   Dependency cũ.
-   Warning không liên quan.
-   Cơ hội refactor.
-   UI có thể cải thiện.
-   Test cũ đang fail nhưng không liên quan.

Không tự sửa.

Ghi lại ngắn gọn trong báo cáo cuối dưới mục "Phát hiện thêm" nếu đáng
chú ý.

------------------------------------------------------------------------

## 18. Báo cáo sau khi hoàn thành

Trả lời ngắn gọn, tập trung vào kết quả.

Format mặc định:

### Đã hoàn thành

**Nguyên nhân / mục tiêu** - ...

**Đã thay đổi** - `path/to/file`: ... - `path/to/file`: ...

**Kiểm tra** - ... - ...

**Phát hiện thêm** (chỉ khi có) - ...

Không cần mô tả lại toàn bộ quá trình suy luận.

------------------------------------------------------------------------

## 19. Quy tắc ưu tiên

Nếu có xung đột, ưu tiên theo thứ tự:

1.  Yêu cầu trực tiếp hiện tại của người dùng.
2.  An toàn dữ liệu và bảo mật.
3.  Instructions cụ thể hơn trong `AGENTS.md` nằm gần file đang sửa hơn.
4.  Quy tắc của repository/project.
5.  File `AGENTS.md` này.
6.  Conventions hiện có trong code.

------------------------------------------------------------------------

## 20. Nguyên tắc mặc định cho vibe coding

Với yêu cầu nhỏ hoặc trung bình, mặc định:

> Hiểu yêu cầu → tìm code liên quan → xác định root cause/implementation
> → sửa tối thiểu → kiểm tra → báo cáo ngắn.

Không mặc định:

> Scan toàn project → redesign → refactor → thêm abstraction → thay
> dependency → sửa các vấn đề phụ.

Mục tiêu là tạo ra thay đổi đúng, nhỏ, dễ kiểm tra và ít gây regression
nhất.
