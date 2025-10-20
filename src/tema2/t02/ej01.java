package tema2.t02;

import java.util.Scanner;

public class ej01 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Introduce la altura del triangulo:");
        int altura = sc.nextInt();

        for (int i = 1; i <= altura; i++){
            for (int j = 1; j <= i; j++) {
                System.out.print("*");
            }
            System.out.println();
        }
    }
}
