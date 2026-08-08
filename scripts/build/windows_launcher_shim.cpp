#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <cwchar>
#include <string>
#include <vector>

namespace {

bool has_argument(const std::vector<std::wstring>& arguments, const wchar_t* expected) {
    for (const std::wstring& argument : arguments) {
        if (_wcsicmp(argument.c_str(), expected) == 0) {
            return true;
        }
    }
    return false;
}

std::wstring quote_argument(const std::wstring& argument) {
    if (!argument.empty() && argument.find_first_of(L" \t\"") == std::wstring::npos) {
        return argument;
    }
    std::wstring result = L"\"";
    size_t backslashes = 0;
    for (wchar_t character : argument) {
        if (character == L'\\') {
            backslashes += 1;
            continue;
        }
        if (character == L'\"') {
            result.append(backslashes * 2 + 1, L'\\');
            result.push_back(L'\"');
            backslashes = 0;
            continue;
        }
        result.append(backslashes, L'\\');
        backslashes = 0;
        result.push_back(character);
    }
    result.append(backslashes * 2, L'\\');
    result.push_back(L'\"');
    return result;
}

std::vector<std::wstring> current_arguments() {
    int count = 0;
    LPWSTR* values = CommandLineToArgvW(GetCommandLineW(), &count);
    if (values == nullptr) {
        return {};
    }
    std::vector<std::wstring> result;
    for (int index = 1; index < count; index += 1) {
        result.emplace_back(values[index]);
    }
    LocalFree(values);
    return result;
}

std::wstring program_directory() {
    std::vector<wchar_t> buffer(32768);
    DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) {
        return {};
    }
    std::wstring path(buffer.data(), length);
    size_t separator = path.find_last_of(L"\\/");
    return separator == std::wstring::npos ? std::wstring() : path.substr(0, separator);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    const std::vector<std::wstring> arguments = current_arguments();
    const wchar_t* hooks[] = {
        L"--veloapp-install",
        L"--veloapp-updated",
        L"--veloapp-obsolete",
        L"--veloapp-uninstall",
    };
    for (const wchar_t* hook : hooks) {
        if (has_argument(arguments, hook)) {
            return 0;
        }
    }

    const std::wstring directory = program_directory();
    if (directory.empty()) {
        return 2;
    }
    const std::wstring application = directory + L"\\DicePP-App.exe";
    if (GetFileAttributesW(application.c_str()) == INVALID_FILE_ATTRIBUTES) {
        return 2;
    }

    std::wstring command_line = quote_argument(application);
    for (const std::wstring& argument : arguments) {
        command_line.push_back(L' ');
        command_line.append(quote_argument(argument));
    }
    std::vector<wchar_t> mutable_command(command_line.begin(), command_line.end());
    mutable_command.push_back(L'\0');

    if (!SetEnvironmentVariableW(L"PYINSTALLER_RESET_ENVIRONMENT", L"1")) {
        return 3;
    }
    const bool detached = arguments.empty()
        || has_argument(arguments, L"--background")
        || has_argument(arguments, L"--manager-tray");
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    if (!detached) {
        startup.dwFlags |= STARTF_USESTDHANDLES;
        startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
        startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    }
    PROCESS_INFORMATION process{};
    const BOOL started = CreateProcessW(
        application.c_str(),
        mutable_command.data(),
        nullptr,
        nullptr,
        TRUE,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        nullptr,
        directory.c_str(),
        &startup,
        &process);
    if (!started) {
        return 3;
    }
    CloseHandle(process.hThread);

    if (detached) {
        CloseHandle(process.hProcess);
        return 0;
    }

    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hProcess);
    return static_cast<int>(exit_code);
}
