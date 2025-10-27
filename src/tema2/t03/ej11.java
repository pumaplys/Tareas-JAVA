package tema2.t03;

import java.util.Scanner;

public class ej11 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Digite un numero:");
        int a = sc.nextInt();
        System.out.println("Digite otro numero");
        int b = sc.nextInt();
        System.out.println("Digite otro numero");
        int c = sc.nextInt();
        sc.close();

        System.out.println(multiplicar(a, b));
        System.out.println(multiplicar(a, b, c));

    }

    static int multiplicar(int a, int b) {
        return a * b;
    }

    static int multiplicar(int a, int b, int c) {
        return a * b * c;
    }
}
