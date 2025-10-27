package tema2.t03;

import java.util.Scanner;

public class ej08 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Digita un numero entero: ");
        int n = sc.nextInt();
        sc.close();

        int resul = ValorAbsoluto(n);

        System.out.println("El valor absoluto de " + n + " es: " + resul);
    }

    static int ValorAbsoluto(int n) {
        if (n >= 0) {
            return n;
        } else {
            return n * -1;
        }
    }
}
